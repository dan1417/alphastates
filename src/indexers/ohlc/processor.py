"""Blockchain data processing logic for OHLC indexer."""

import asyncio
import atexit
import json
import logging
import os
import signal
import time
import concurrent.futures

import bittensor as bt
from bittensor.core.chain_data.dynamic_info import DynamicInfo
from bittensor.core.chain_data.subnet_identity import SubnetIdentity
from bittensor.core.chain_data.utils import decode_account_id
from bittensor.core.subtensor import StakeInfo
from bittensor.utils.balance import Balance, fixed_to_float

from async_substrate_interface.errors import SubstrateRequestException

from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select

from src.common.cache import get_cache
from src.common.config import settings
from src.common.models import Trade, OHLCInterval, OHLC_MODELS, IndexerState

logger = logging.getLogger(__name__)

_process_subtensor = None

class BlockchainProcessor:
    """Process and index blockchain data from Bittensor network."""

    INDEXER_NAME = "ohlc"

    def __init__(self, session: AsyncSession):
        self.session = session
        self.cache = None
        self.subtensor = None
        self.running = False
        self.candle_cache = {}  # In-memory cache for candle data during batch processing

    async def initialize(self) -> None:
        """Initialize processor dependencies."""
        self.cache = await get_cache()
        self.subtensor = bt.Subtensor(settings.rpc_url)

        logger.info(f"Using {settings.max_workers} workers")
        logger.info(f"Using {settings.batch_size} blocks per batch")
        logger.info(f"Using {settings.dtao_start_block} as start block")
        logger.info(f"Using {settings.rpc_url} as RPC URL")
        logger.info(f"Using {settings.redis_url} as Redis URL")
        logger.info(f"Using {settings.database_url} as Database URL")

    async def _load_indexer_state(self) -> int:
        """
        Load the last indexed block from the indexer state table.
        
        Returns:
            The last indexed block number, or dtao_start_block if no state exists.
        """
        stmt = select(IndexerState).where(IndexerState.indexer_name == self.INDEXER_NAME)
        result = await self.session.execute(stmt)
        state = result.scalar_one_or_none()
        
        if state is None:
            logger.info(f"No previous state found, starting from block {settings.dtao_start_block}")
            return settings.dtao_start_block
        
        logger.info(f"Loaded state: last indexed block = {state.last_indexed_block}, status = {state.status}")
        return state.last_indexed_block + 1

    async def _save_indexer_state(self, block_number: int, status: str = "running") -> None:
        """
        Save the current indexer state to the database.
        
        Args:
            block_number: The last successfully indexed block number.
            status: The current status of the indexer (running, stopped, error).
        """
        stmt = select(IndexerState).where(IndexerState.indexer_name == self.INDEXER_NAME)
        result = await self.session.execute(stmt)
        state = result.scalar_one_or_none()
        
        if state is None:
            # Create new state
            state = IndexerState(
                indexer_name=self.INDEXER_NAME,
                last_indexed_block=block_number,
                last_updated_at=datetime.now(),
                status=status
            )
            self.session.add(state)
        else:
            # Update existing state
            state.last_indexed_block = block_number
            state.last_updated_at = datetime.now()
            state.status = status

    async def reset_database(self) -> None:
        """Reset the database by deleting all rows from all tables and clearing Redis cache."""
        logger.warning("🔄 RESET_DB is enabled - deleting all data from database and cache...")
        
        # Delete all trades
        stmt = delete(Trade)
        await self.session.execute(stmt)
        logger.info("   • Deleted all trades")
        
        # Delete all OHLC data for each interval
        for interval, ohlc_model in OHLC_MODELS.items():
            stmt = delete(ohlc_model)
            await self.session.execute(stmt)
            logger.info(f"   • Deleted all {interval.value} candles")
        
        # Delete indexer state
        stmt = delete(IndexerState).where(IndexerState.indexer_name == self.INDEXER_NAME)
        await self.session.execute(stmt)
        logger.info("   • Deleted indexer state")
        
        # Commit the deletions
        await self.session.commit()
        
        # Clear Redis cache
        if self.cache is not None:
            # Delete all OHLC cached candles
            for interval in OHLCInterval:
                redis_key = f"ohlc_{interval.value}_last_indexed_candle"
                await self.cache.delete(redis_key)
            logger.info("   • Cleared Redis cache")
        
        logger.info("✅ Database reset complete")

    async def run(self) -> None:
        """Sync indexer to the current chain head."""
        # 1) Get latest indexed block from indexer state table
        current_block = await self._load_indexer_state()

        # 2) Process all blocks in between
        total_blocks_processed = 0
        total_events_processed = 0

        self.running = True
        
        while self.running:
            try:
                logger.info(f"Waiting for block: {current_block}")
                self.subtensor.wait_for_block(current_block)

                end_block = self.get_latest_block()
                logger.info(f"Latest block: {end_block}")

                results = []
                batch_start_time = time.time()
                is_latest_block = current_block == end_block
                batch_start = current_block
                batch_end = min(current_block + settings.batch_size - 1, end_block)
                
                if is_latest_block: # indexing latest block so don't need to use multi-threads
                    results.append(_fetch_block_data(current_block, self.subtensor))
                else:
                    # Calculate batch range
                    batch_number = ((batch_start - settings.dtao_start_block) // settings.batch_size) + 1
                    total_batches = ((end_block - settings.dtao_start_block) // settings.batch_size) + 1
                    
                    logger.info(f"{'─'*80}")
                    logger.info(f"📦 Batch {batch_number}/{total_batches}: Processing blocks {batch_start} to {batch_end}")
                    logger.info(f"{'─'*80}")
                    
                    # Fetch blocks in this batch
                    executor = concurrent.futures.ProcessPoolExecutor(max_workers=settings.max_workers)
                    future_to_block = {executor.submit(_fetch_block_data, block_number): block_number 
                                    for block_number in range(batch_start, batch_end + 1)}
                    
                    # Collect results and fail fast on any error
                    for future in concurrent.futures.as_completed(future_to_block):
                        block_number = future_to_block[future]
                        try:
                            result = future.result()
                            results.append(result)
                        except Exception as exc:
                            logger.error(f'❌ Block {block_number} generated an exception: {exc}')
                            # Cancel all pending futures
                            for f in future_to_block:
                                f.cancel()
                            executor.shutdown(wait=False, cancel_futures=True)
                            # Save error state and stop
                            await self._save_indexer_state(current_block - 1, status="error")
                            await self.session.commit()
                            logger.error(f"❌ Batch processing failed. Indexer state saved at block {current_block - 1}")
                            raise RuntimeError(f"Batch processing failed at block {block_number}") from exc
                    
                    # Shutdown executor after batch
                    executor.shutdown(wait=True, cancel_futures=True)
                    executor = None
                    future_to_block = {}
                    
                batch_fetch_time = time.time() - batch_start_time
                
                # Process stake events for this batch
                if len(results) > 0:
                    logger.info(f"⚙️  Processing {len(results)} blocks with {sum(len(r[1]) for r in results)} events...")
                    process_start_time = time.time()
                    sorted_results = sorted(results, key=lambda x: x[0])
                    
                    try:
                        await self._process_events_batch(sorted_results)
                    except Exception as exc:
                        logger.error(f'❌ Failed to process events batch: {exc}')
                        # Save error state and stop
                        await self.session.rollback()
                        await self._save_indexer_state(current_block - 1, status="error")
                        await self.session.commit()
                        logger.error(f"❌ Event processing failed. Indexer state saved at block {current_block - 1}")
                        raise RuntimeError(f"Event processing failed for batch starting at block {batch_start}") from exc
                    
                    process_time = time.time() - process_start_time
                    
                    # Update statistics
                    batch_events = sum(len(r[1]) for r in results)
                    total_blocks_processed += len(results)
                    total_events_processed += batch_events
                    
                    if is_latest_block:
                        batch_total_time = time.time() - batch_start_time
                        logger.info(f"📊 Process Statistics:")
                        logger.info(f"   • Blocks processed: {len(results)}")
                        logger.info(f"   • Events processed: {batch_events}")
                        logger.info(f"   • Fetch time: {batch_fetch_time:.2f}s")
                        logger.info(f"   • Process time: {process_time:.2f}s")
                        logger.info(f"   • Total time: {batch_total_time:.2f}s")
                        logger.info(f"   • Avg time per block: {batch_total_time/len(results):.3f}s")
                        logger.info("-"*80)
                    else:
                        batch_total_time = time.time() - batch_start_time
                        logger.info(f"📊 Batch {batch_number} Statistics:")
                        logger.info(f"   • Blocks processed: {len(results)}")
                        logger.info(f"   • Events processed: {batch_events}")
                        logger.info(f"   • Fetch time: {batch_fetch_time:.2f}s")
                        logger.info(f"   • Process time: {process_time:.2f}s")
                        logger.info(f"   • Total batch time: {batch_total_time:.2f}s")
                        logger.info(f"   • Avg time per block: {batch_total_time/len(results):.3f}s")
                    
                    # Save indexer state after successful batch processing
                    await self._save_indexer_state(batch_end, status="running")
                    await self.session.commit()
                else:
                    logger.info(f"⚠️  No blocks were processed in this batch")
                
                # Move to next batch
                current_block = batch_end + 1
                
            except Exception as exc:
                # Any unhandled exception should stop the indexer
                logger.exception(f"❌ Fatal error in indexer main loop: {exc}")
                await self._save_indexer_state(current_block - 1, status="error")
                await self.session.commit()
                raise
        
    def get_latest_block(self) -> int:
        """Get the latest block from the blockchain."""
        latest_block = self.subtensor.get_current_block()
        return latest_block

    async def _get_cached_candle(self, interval: str) -> dict | None:
        """
        Get the last indexed candle from in-memory cache.
        During batch processing, all data is kept in memory.
        
        Args:
            interval: The interval key (e.g., "1m", "5m")
            
        Returns:
            Dict with structure: {
                "open_time": timestamp, 
                "last_processed_block": block_number, 
                "prices": {netuid: {open, high, low, close, volume_tao, volume_alpha, trade_count}}
            }
            or None if not cached
        """
        redis_key = f"ohlc_{interval}_last_indexed_candle"
        
        # Return from in-memory cache (loaded at batch start)
        return self.candle_cache.get(redis_key)
    
    async def _save_cached_candle(self, interval: str, candle_data: dict) -> None:
        """
        Save the current candle to in-memory cache.
        Will be written to Redis at the end of the batch.
        
        Args:
            interval: The interval key (e.g., "1m", "5m")
            candle_data: Dict with structure: {
                "open_time": timestamp, 
                "last_processed_block": block_number, 
                "prices": {netuid: {open, high, low, close, volume_tao, volume_alpha, trade_count}}
            }
        """
        redis_key = f"ohlc_{interval}_last_indexed_candle"
        
        # Save to in-memory cache (will be written to Redis at batch end)
        self.candle_cache[redis_key] = candle_data
    
    async def _write_candles_to_redis(self) -> None:
        """
        Write all candles from in-memory cache back to Redis.
        Implements retry logic for transient Redis failures.
        """
        if self.cache is None:
            return
        
        max_retries = 3
        retry_delay = 1  # seconds
        
        for attempt in range(max_retries):
            try:
                # Write each candle to Redis
                for redis_key, candle_data in self.candle_cache.items():
                    await self.cache.set(redis_key, json.dumps(candle_data))
                
                logger.info(f"✅ Wrote {len(self.candle_cache)} candles to Redis successfully")
                break  # Success, exit retry loop
                
            except Exception as exc:
                from redis.exceptions import ReadOnlyError, ConnectionError, TimeoutError
                
                # Check if this is a read-only error
                is_readonly_error = isinstance(exc, ReadOnlyError) or "read only" in str(exc).lower()
                is_connection_error = isinstance(exc, (ConnectionError, TimeoutError))
                
                if is_readonly_error:
                    logger.error(f"❌ Redis is in READ-ONLY mode (attempt {attempt + 1}/{max_retries}): {exc}")
                    
                    # Check Redis replication status
                    try:
                        from src.common.cache import is_redis_readonly
                        if await is_redis_readonly():
                            logger.error("Redis connected to replica instead of master - connection issue detected")
                    except Exception as check_err:
                        logger.error(f"Failed to check Redis status: {check_err}")
                        
                elif is_connection_error:
                    logger.error(f"❌ Redis connection error (attempt {attempt + 1}/{max_retries}): {exc}")
                else:
                    logger.error(f"❌ Redis write failed (attempt {attempt + 1}/{max_retries}): {exc}")
                
                # If this was the last attempt, raise the error
                if attempt == max_retries - 1:
                    logger.error(f"❌ Redis write failed after {max_retries} attempts")
                    raise RuntimeError("Redis write operation failed after retries") from exc
                
                # Wait before retrying (exponential backoff)
                wait_time = retry_delay * (2 ** attempt)
                logger.info(f"⏳ Retrying Redis write in {wait_time} seconds...")
                await asyncio.sleep(wait_time)
    
    async def _save_candle_to_db(self, interval: OHLCInterval, open_time: int, prices: dict) -> None:
        """
        Save a completed candle to PostgreSQL.
        
        Args:
            interval: The OHLC interval enum
            open_time: The candle open timestamp (seconds)
            prices: Dict mapping netuid to {open, high, low, close, volume_tao, volume_alpha, trade_count}
        """
        ohlc_model = OHLC_MODELS[interval]
        candles_to_add = []
        
        for netuid, ohlc_data in prices.items():
            candle = ohlc_model(
                netuid=int(netuid),
                open_time=datetime.fromtimestamp(open_time),
                open=ohlc_data["open"],
                high=ohlc_data["high"],
                low=ohlc_data["low"],
                close=ohlc_data["close"],
                volume_tao=ohlc_data.get("volume_tao", 0),
                volume_alpha=ohlc_data.get("volume_alpha", 0),
                trade_count=ohlc_data.get("trade_count", 0),
            )
            candles_to_add.append(candle)
        
        if candles_to_add:
            self.session.add_all(candles_to_add)

    async def _process_events_batch(self, sorted_results: list[tuple[int, list[dict], dict, int]]) -> None:
        """
        Process all events from sorted_results as a single batch transaction.
        Events are processed in chronological order to handle NetworkRemoved correctly.
        All candle data is loaded into memory at start and written back at end.

        Args:
            sorted_results: List of (block_number, events) tuples sorted by block number.
        """
        # Load all candles from Redis into memory at batch start
        if self.cache is not None:
            self.candle_cache = {}
            for interval in OHLCInterval:
                redis_key = f"ohlc_{interval.value}_last_indexed_candle"
                cached_data = await self.cache.get(redis_key)
                if cached_data is not None:
                    self.candle_cache[redis_key] = json.loads(cached_data)
                    logger.debug(f"Loaded {interval.value} candle from Redis")
            
            logger.info(f"Loaded {len(self.candle_cache)} candles from Redis into memory")
        
        trades_to_add: list[Trade] = []

        # Process events in chronological order
        for block_number, events, subnet_prices, timestamp in sorted_results:
            # Track volumes and trade counts per netuid for this block
            block_volumes: dict[int, dict] = {}
            
            for event in events:
                event_id = event['event_id']

                # if event_id == 'NetworkRemoved':
                #     netuid = event['netuid']
                #     if trades_to_add:
                #         self.session.add_all(trades_to_add)
                #         trades_to_add = []

                #     stmt = delete(Trade).where(Trade.netuid == netuid)
                #     await self.session.execute(stmt)
                #     for ohlc_model in OHLC_MODELS.values():
                #         stmt = delete(ohlc_model).where(ohlc_model.netuid == netuid)
                #         await self.session.execute(stmt)

                if event_id in ('StakeAdded', 'StakeRemoved'):
                    netuid = event['netuid']
                    trade = Trade(
                        is_sell=event['is_sell'],
                        netuid=netuid,
                        tao=event['tao'],
                        alpha=event['alpha'],
                        price=event['price'],
                        address=event['address'],
                        block_number=event['block_number'],
                        block_timestamp=datetime.fromtimestamp(event['block_timestamp']),
                        block_hash=event['block_hash'],
                        extrinsic_index=event['extrinsic_index'],
                        is_transferred=event['is_transferred'],
                        is_moved=event['is_moved'],
                        is_pure_move=event['is_pure_move'],
                        is_burned=False,
                        is_coldkey_swapped=False,
                    )
                    trades_to_add.append(trade)
                    
                    # Accumulate volumes for this netuid
                    if netuid not in block_volumes:
                        block_volumes[netuid] = {
                            "volume_tao": 0,
                            "volume_alpha": 0,
                            "trade_count": 0
                        }
                    block_volumes[netuid]["volume_tao"] += event['tao']
                    block_volumes[netuid]["volume_alpha"] += event['alpha']
                    block_volumes[netuid]["trade_count"] += 1

                elif event_id == 'AlphaBurned':
                    trade = Trade(
                        is_sell=True,
                        netuid=event['netuid'],
                        tao=0,
                        alpha=event['amount'],
                        price=0,
                        address=event['address'],
                        block_number=event['block_number'],
                        block_timestamp=datetime.fromtimestamp(event['block_timestamp']),
                        block_hash=event['block_hash'],
                        extrinsic_index=event['extrinsic_index'],
                        is_transferred=False,
                        is_moved=False,
                        is_burned=True,
                        is_coldkey_swapped=False,
                    )
                    trades_to_add.append(trade)

                elif event_id == 'ColdkeySwapped':
                    old_address = event['old_address']
                    new_address = event['new_address']
                    subnet_alpha = event['subnet_alpha']
                    for netuid, alpha in subnet_alpha.items():
                        price = subnet_prices[netuid]
                        tao = price / 10**9 * alpha
                        sell_trade = Trade(
                            is_sell=True,
                            netuid=netuid,
                            tao=tao,
                            alpha=alpha,
                            price=price,
                            address=old_address,
                            block_number=event['block_number'],
                            block_timestamp=datetime.fromtimestamp(event['block_timestamp']),
                            block_hash=event['block_hash'],
                            extrinsic_index=event['extrinsic_index'],
                            is_transferred=False,
                            is_moved=False,
                            is_burned=False,
                            is_coldkey_swapped=True,
                        )
                        buy_trade = Trade(
                            is_sell=False,
                            netuid=netuid,
                            tao=tao,
                            alpha=alpha,
                            price=price,
                            address=new_address,
                            block_number=event['block_number'],
                            block_timestamp=datetime.fromtimestamp(event['block_timestamp']),
                            block_hash=event['block_hash'],
                            extrinsic_index=event['extrinsic_index'],
                            is_transferred=False,
                            is_moved=False,
                            is_burned=False,
                            is_coldkey_swapped=True,
                        )
                        trades_to_add.append(sell_trade)
                        trades_to_add.append(buy_trade)
                
            # Update OHLC data for all intervals
            intervals = {
                OHLCInterval.MIN_1: 60,
                OHLCInterval.MIN_5: 300,
                OHLCInterval.MIN_15: 900,
                OHLCInterval.HOUR_1: 3600,
                OHLCInterval.HOUR_4: 14400,
                OHLCInterval.DAY_1: 86400,
                OHLCInterval.DAY_7: 604800,
            }

            for interval, seconds in intervals.items():
                interval_key = interval.value
                open_time = int((timestamp // seconds) * seconds)
                
                # Get cached candle data from Redis
                cached_candle = await self._get_cached_candle(interval_key)
                
                # Skip if this block has already been processed for this interval
                if cached_candle is not None and block_number <= cached_candle.get("last_processed_block", 0):
                    continue
                
                if cached_candle is None:
                    # Initialize new candle with current prices
                    new_candle = {
                        "open_time": open_time,
                        "last_processed_block": block_number,
                        "prices": {}
                    }
                    for netuid, price in subnet_prices.items():
                        netuid_str = str(netuid)
                        volumes = block_volumes.get(netuid, {})
                        new_candle["prices"][netuid_str] = {
                            "open": price,
                            "high": price,
                            "low": price,
                            "close": price,
                            "volume_tao": volumes.get("volume_tao", 0),
                            "volume_alpha": volumes.get("volume_alpha", 0),
                            "trade_count": volumes.get("trade_count", 0)
                        }
                    await self._save_cached_candle(interval_key, new_candle)
                
                elif cached_candle["open_time"] == open_time:
                    # Update existing candle
                    for netuid, price in subnet_prices.items():
                        netuid_str = str(netuid)
                        volumes = block_volumes.get(netuid, {})
                        
                        if netuid_str in cached_candle["prices"]:
                            # Update OHLC data
                            cached_candle["prices"][netuid_str]["high"] = max(
                                cached_candle["prices"][netuid_str]["high"], price
                            )
                            cached_candle["prices"][netuid_str]["low"] = min(
                                cached_candle["prices"][netuid_str]["low"], price
                            )
                            cached_candle["prices"][netuid_str]["close"] = price
                            # Accumulate volumes
                            cached_candle["prices"][netuid_str]["volume_tao"] = (
                                cached_candle["prices"][netuid_str].get("volume_tao", 0) + volumes.get("volume_tao", 0)
                            )
                            cached_candle["prices"][netuid_str]["volume_alpha"] = (
                                cached_candle["prices"][netuid_str].get("volume_alpha", 0) + volumes.get("volume_alpha", 0)
                            )
                            cached_candle["prices"][netuid_str]["trade_count"] = (
                                cached_candle["prices"][netuid_str].get("trade_count", 0) + volumes.get("trade_count", 0)
                            )
                        else:
                            # New netuid appeared, initialize it
                            cached_candle["prices"][netuid_str] = {
                                "open": price,
                                "high": price,
                                "low": price,
                                "close": price,
                                "volume_tao": volumes.get("volume_tao", 0),
                                "volume_alpha": volumes.get("volume_alpha", 0),
                                "trade_count": volumes.get("trade_count", 0)
                            }
                    # Update last processed block
                    cached_candle["last_processed_block"] = block_number
                    await self._save_cached_candle(interval_key, cached_candle)
                
                else:
                    # New candle - update old candle's close prices to current prices, save it, then start new candle
                    # Update old candle's close prices to current prices before saving
                    for netuid, price in subnet_prices.items():
                        netuid_str = str(netuid)
                        if netuid_str in cached_candle["prices"]:
                            cached_candle["prices"][netuid_str]["close"] = price
                    
                    # Save the updated old candle to DB
                    await self._save_candle_to_db(interval, cached_candle["open_time"], cached_candle["prices"])
                    
                    # Initialize new candle with current block data
                    # Use current price as open price for new candle
                    new_candle = {
                        "open_time": open_time,
                        "last_processed_block": block_number,
                        "prices": {}
                    }
                    for netuid, price in subnet_prices.items():
                        netuid_str = str(netuid)
                        volumes = block_volumes.get(netuid, {})
                        
                        new_candle["prices"][netuid_str] = {
                            "open": price,
                            "high": price,
                            "low": price,
                            "close": price,
                            "volume_tao": volumes.get("volume_tao", 0),
                            "volume_alpha": volumes.get("volume_alpha", 0),
                            "trade_count": volumes.get("trade_count", 0)
                        }
                    await self._save_cached_candle(interval_key, new_candle)

        if trades_to_add:
            self.session.add_all(trades_to_add)

        # Write all candles from memory back to Redis
        logger.info(f"Writing candles to Redis...")
        redis_start_time = time.time()
        await self._write_candles_to_redis()
        logger.info(f"Wrote candles to Redis in {time.time() - redis_start_time:.2f} seconds")

        # Single commit for the entire batch
        logger.info(f"Committing batch to database...")
        db_start_time = time.time()
        await self.session.commit()
        logger.info(f"Committed batch to database in {time.time() - db_start_time:.2f} seconds")
        
        # Clear in-memory cache after batch is complete
        self.candle_cache = {}

    def stop(self):
        self.running = False

def _cleanup_subtensor():
    """Clean up the process-local subtensor instance."""
    global _process_subtensor
    if _process_subtensor is not None:
        try:
            # Close the substrate connection if it exists
            if hasattr(_process_subtensor, 'substrate') and _process_subtensor.substrate:
                # Close websocket connection
                if hasattr(_process_subtensor.substrate, 'websocket') and _process_subtensor.substrate.websocket:
                    _process_subtensor.substrate.websocket.close()
                # Close the substrate itself
                _process_subtensor.substrate.close()
        except Exception as e:
            pass  # Ignore cleanup errors
        finally:
            _process_subtensor = None

def _signal_handler_worker(signum, frame):
    """Signal handler for worker processes."""
    _cleanup_subtensor()
    os._exit(0)

def _get_process_subtensor():
    """Get or create a subtensor instance for this process."""
    global _process_subtensor
    if _process_subtensor is None:
        _process_subtensor = bt.Subtensor(settings.rpc_url)
        atexit.register(_cleanup_subtensor)
    return _process_subtensor

def _fetch_block_data(block_number, subtensor = None):
    """Fetch block data using process-local subtensor instance."""
    # Register signal handler for this worker process
    signal.signal(signal.SIGINT, _signal_handler_worker)
    signal.signal(signal.SIGTERM, _signal_handler_worker)
    
    if subtensor is None:
        subtensor = _get_process_subtensor()
    if block_number % 100 == 0:
        logger.info(f"Processing block {block_number}")
    block = subtensor.substrate.get_block_header(block_number=block_number)
    block_hash = block['header']['hash']
    events = subtensor.substrate.get_events(block_hash=block_hash)
    timestamp = round(subtensor.get_timestamp(block_number).timestamp(), 0)

    # Get subnet prices (referenced from subtensor sdk)
    query = subtensor.substrate.runtime_call(
        api="SubnetInfoRuntimeApi",
        method="get_all_dynamic_info",
        block_hash=block_hash,
    )
    decoded = query.decode()
    try:
        subnet_prices = subtensor.get_subnet_prices(block=block_number)
        for sn in decoded:
            sn.update(
                {"price": subnet_prices.get(sn["netuid"], Balance.from_tao(0))}
            )
    except (SubstrateRequestException, ValueError) as e:
        pass
    subnet_prices = {subnet["netuid"]: _from_dict(subnet).price.rao for subnet in decoded}

    filtered_events = []
    num_events = len(events)
    for i, event in enumerate(events):
        event_id = event['event']['event_id']
        if event_id in ['StakeAdded', 'StakeRemoved']:
            if event['event']['attributes'][2] == 0:
                continue
            if event['event']['attributes'][3] == 0:
                continue
            is_transferred = False
            is_moved = False
            is_pure_move = False

            offset = None
            if event_id == 'StakeRemoved':
                if i + 2 < num_events and (events[i + 2]['event']['event_id'] in ['StakeTransferred', 'StakeMoved']):
                    offset = 2
                if i + 3 < num_events and (events[i + 3]['event']['event_id'] in ['StakeTransferred', 'StakeMoved']):
                    offset = 3
            if event_id == 'StakeAdded':
                if i + 1 < num_events and (events[i + 1]['event']['event_id'] in ['StakeTransferred', 'StakeMoved']):
                    if events[i - 1]['event']['event_id'] == 'StakeRemoved':
                        offset = 2
                    if events[i - 2]['event']['event_id'] == 'StakeRemoved':
                        offset = 3
            if offset == 2:
                if event_id == 'StakeRemoved' and i + 2 < num_events:
                    if events[i + 2]['event']['event_id'] == 'StakeTransferred':
                        is_transferred = True
                    if events[i + 2]['event']['event_id'] == 'StakeMoved':
                        is_moved = True
                        if events[i]['event']['attributes'][4] == events[i + 1]['event']['attributes'][4]:
                            is_pure_move = True
                if event_id == 'StakeAdded' and i + 1 < num_events:
                    if events[i + 1]['event']['event_id'] == 'StakeTransferred':
                        is_transferred = True
                    if events[i + 1]['event']['event_id'] == 'StakeMoved':
                        is_moved = True
                        if events[i]['event']['attributes'][4] == events[i - 1]['event']['attributes'][4]:
                            is_pure_move = True
            if offset == 3:
                # StakeRemoved -> Deposit -> StakeAdded -> StakeMoved/StakeTransferred    
                if event_id == 'StakeRemoved' and i + 3 < num_events:
                    if events[i + 3]['event']['event_id'] == 'StakeTransferred':
                        is_transferred = True
                    if events[i + 3]['event']['event_id'] == 'StakeMoved':
                        is_moved = True
                        if events[i]['event']['attributes'][4] == events[i + 2]['event']['attributes'][4]:
                            is_pure_move = True
                if event_id == 'StakeAdded' and i + 1 < num_events:
                    if events[i + 1]['event']['event_id'] == 'StakeTransferred':
                        is_transferred = True
                    if events[i + 1]['event']['event_id'] == 'StakeMoved':
                        is_moved = True
                        if events[i]['event']['attributes'][4] == events[i - 2]['event']['attributes'][4]:
                            is_pure_move = True
                            
            filtered_events.append({
                'event_id': event_id,
                'is_sell': event_id == 'StakeRemoved',
                'netuid': event['event']['attributes'][4],
                'tao': event['event']['attributes'][2],
                'alpha': event['event']['attributes'][3],
                'price': round(10**9 * event['event']['attributes'][2] / event['event']['attributes'][3], 0),
                'address': event['event']['attributes'][0],
                'block_number': block_number,
                'block_timestamp': round(timestamp, 0),
                'block_hash': block_hash,
                'extrinsic_index': event['extrinsic_idx'],
                'is_transferred': is_transferred,
                'is_moved': is_moved,
                'is_pure_move': is_pure_move,
            })
        if event_id == 'NetworkRemoved':
            logger.info(f"NetworkRemoved event: {event['event']['attributes']}")
            netuid = event['event']['attributes']
            if isinstance(netuid, list):
                netuid = netuid[0]
            filtered_events.append({
                'event_id': event_id,
                'block': block_number,
                'netuid': netuid,
            })
        if event_id == 'AlphaBurned':
            logger.info(f"AlphaBurned event: {event['event']['attributes']}")
            address = event['event']['attributes'][0]
            amount = event['event']['attributes'][2]
            netuid = event['event']['attributes'][3]
            filtered_events.append({
                'event_id': event_id,
                'address': address,
                'amount': amount,
                'netuid': netuid,
                'block_number': block_number,
                'block_timestamp': round(timestamp, 0),
                'block_hash': block_hash,
                'extrinsic_index': event['extrinsic_idx'],
            })
        if event_id == 'ColdkeySwapped':
            logger.info(f"ColdkeySwapped Raw Event: {event['event']['attributes']}")
            if 'old_coldkey' in event['event']['attributes']:
                old_address = event['event']['attributes']['old_coldkey']
                new_address = event['event']['attributes']['new_coldkey']
            else:
                old_address = event['event']['attributes'][0]
                new_address = event['event']['attributes'][1]
            logger.info(f"ColdkeySwapped Extracted Addresses: {old_address} -> {new_address}")
            
            stake_info: list[StakeInfo] = subtensor.get_stake_info_for_coldkey(coldkey_ss58=new_address, block=block_number)
            subnet_alpha = {}
            for stake in stake_info:
                netuid = stake.netuid
                alpha = stake.stake.rao
                if netuid not in subnet_alpha:
                    subnet_alpha[netuid] = alpha
                else:
                    subnet_alpha[netuid] += alpha
            logger.info(f"ColdkeySwapped Alpha: {len(subnet_alpha.items())} Subnets Found")

            filtered_events.append({
                'event_id': event_id,
                'old_address': old_address,
                'new_address': new_address,
                'subnet_alpha': subnet_alpha,
                'block_number': block_number,
                'block_timestamp': round(timestamp, 0),
                'block_hash': block_hash,
                'extrinsic_index': -1 if event['extrinsic_idx'] is None else event['extrinsic_idx'],
            })

    return (block_number, filtered_events, subnet_prices, timestamp)

def _from_dict(decoded: dict) -> DynamicInfo:
    """Returns a DynamicInfo object from decoded chain data."""

    netuid = int(decoded["netuid"])
    symbol = bytes([int(b) for b in decoded["token_symbol"]]).decode()
    subnet_name = bytes([int(b) for b in decoded["subnet_name"]]).decode()

    is_dynamic = (
        True if int(decoded["netuid"]) > 0 else False
    )  # Root is not dynamic

    owner_hotkey = decode_account_id(decoded["owner_hotkey"])
    owner_coldkey = decode_account_id(decoded["owner_coldkey"])

    emission = Balance.from_rao(decoded["emission"]).set_unit(0)
    alpha_in = Balance.from_rao(decoded["alpha_in"]).set_unit(netuid)
    alpha_out = Balance.from_rao(decoded["alpha_out"]).set_unit(netuid)
    tao_in = Balance.from_rao(decoded["tao_in"]).set_unit(0)
    alpha_out_emission = Balance.from_rao(decoded["alpha_out_emission"]).set_unit(
        netuid
    )
    alpha_in_emission = Balance.from_rao(decoded["alpha_in_emission"]).set_unit(
        netuid
    )
    tao_in_emission = Balance.from_rao(decoded["tao_in_emission"]).set_unit(0)
    pending_alpha_emission = Balance.from_rao(
        decoded["pending_alpha_emission"]
    ).set_unit(netuid)
    pending_root_emission = Balance.from_rao(
        decoded["pending_root_emission"]
    ).set_unit(0)

    subnet_volume = Balance.from_rao(decoded["subnet_volume"]).set_unit(netuid)

    if subnet_identity := decoded.get("subnet_identity"):
        # we need to check it for keep backwards compatibility
        logo_bytes = subnet_identity.get("logo_url")
        si_logo_url = bytes(logo_bytes).decode() if logo_bytes else None

        subnet_identity = SubnetIdentity(
            subnet_name=bytes(subnet_identity["subnet_name"]).decode(),
            github_repo=bytes(subnet_identity["github_repo"]).decode(),
            subnet_contact=bytes(subnet_identity["subnet_contact"]).decode(),
            subnet_url=bytes(subnet_identity["subnet_url"]).decode(),
            logo_url=si_logo_url,
            discord=bytes(subnet_identity["discord"]).decode(),
            description=bytes(subnet_identity["description"]).decode(),
            additional=bytes(subnet_identity["additional"]).decode(),
        )
    else:
        subnet_identity = None

    price = decoded.get("price", None)

    if price and not isinstance(price, Balance):
        raise ValueError(f"price must be a Balance object, got {type(price)}.")

    return DynamicInfo(
        netuid=netuid,
        owner_hotkey=owner_hotkey,
        owner_coldkey=owner_coldkey,
        subnet_name=subnet_name,
        symbol=symbol,
        tempo=int(decoded["tempo"]),
        last_step=int(decoded["last_step"]),
        blocks_since_last_step=int(decoded["blocks_since_last_step"]),
        emission=emission,
        alpha_in=alpha_in,
        alpha_out=alpha_out,
        tao_in=tao_in,
        k=tao_in.rao * alpha_in.rao,
        is_dynamic=is_dynamic,
        price=(
            Balance.from_tao(1) if netuid == 0 else
            price if price is not None
            else Balance.from_tao(0) if alpha_in.tao == 0
            else Balance.from_tao(min(tao_in.tao / alpha_in.tao, 10000)).set_unit(netuid)
        ),
        alpha_out_emission=alpha_out_emission,
        alpha_in_emission=alpha_in_emission,
        tao_in_emission=tao_in_emission,
        pending_alpha_emission=pending_alpha_emission,
        pending_root_emission=pending_root_emission,
        network_registered_at=int(decoded["network_registered_at"]),
        subnet_identity=subnet_identity,
        subnet_volume=subnet_volume,
        moving_price=fixed_to_float(decoded["moving_price"], 32),
    )

