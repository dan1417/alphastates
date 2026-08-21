"""Blockchain data processing logic for Proxy indexer."""

import atexit
import json
import logging
import os
import signal
import time
import concurrent.futures
from concurrent.futures import TimeoutError as FuturesTimeoutError

import bittensor as bt
from bittensor.core.chain_data.dynamic_info import DynamicInfo
from bittensor.core.chain_data.subnet_identity import SubnetIdentity
from bittensor.core.chain_data.utils import decode_account_id
from bittensor.core.subtensor import StakeInfo
from bittensor.utils.balance import Balance, fixed_to_float

from async_substrate_interface.errors import SubstrateRequestException

from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, tuple_

from src.common.config import settings
from src.common.models import Proxy, IndexerState

logger = logging.getLogger(__name__)

_process_subtensor = None

class ProxyProcessor:
    """Process and index proxy events from Bittensor network."""

    INDEXER_NAME = "proxy"

    def __init__(self, session: AsyncSession):
        self.session = session
        self.subtensor = None
        self.running = False

    async def initialize(self) -> None:
        """Initialize processor dependencies."""
        self.subtensor = bt.Subtensor(settings.rpc_url)

        logger.info(f"Using {settings.max_workers} workers")
        logger.info(f"Using {settings.batch_size} blocks per batch")
        logger.info(
            f"Using RPC stall timeout {settings.indexer_rpc_wait_timeout_seconds}s, "
            f"{settings.indexer_rpc_fetch_retries} fetch retries"
        )
        logger.info(f"Using 1 as start block")
        logger.info(f"Using {settings.rpc_url} as RPC URL")
        logger.info(f"Using {settings.database_url} as Database URL")

    async def _load_indexer_state(self) -> int:
        """
        Load the last indexed block from the indexer state table.
        
        Returns:
            The last indexed block number, or proxy_start_block if no state exists.
        """
        proxy_start_block = 1

        stmt = select(IndexerState).where(IndexerState.indexer_name == self.INDEXER_NAME)
        result = await self.session.execute(stmt)
        state = result.scalar_one_or_none()
        
        if state is None:
            logger.info(f"No previous state found, starting from block {proxy_start_block}")
            return proxy_start_block
        
        logger.info(f"Loaded state: last indexed block = {state.last_indexed_block}, status = {state.status}")
        return state.last_indexed_block + 1

    async def _save_indexer_state(self, block_number: int, status: str = "running") -> None:
        stmt = select(IndexerState).where(IndexerState.indexer_name == self.INDEXER_NAME)
        result = await self.session.execute(stmt)
        state = result.scalar_one_or_none()
        
        if state is None:
            state = IndexerState(
                indexer_name=self.INDEXER_NAME,
                last_indexed_block=block_number,
                last_updated_at=datetime.now(),
                status=status
            )
            self.session.add(state)
        else:
            state.last_indexed_block = block_number
            state.last_updated_at = datetime.now()
            state.status = status

    async def reset_database(self) -> None:
        """Reset the database by deleting all rows from all tables."""
        logger.warning("🔄 RESET_DB is enabled - deleting all data from database...")
        
        # Delete all proxies
        stmt = delete(Proxy)
        await self.session.execute(stmt)
        logger.info("   • Deleted all proxies")
        
        # Delete indexer state
        stmt = delete(IndexerState).where(IndexerState.indexer_name == self.INDEXER_NAME)
        await self.session.execute(stmt)
        logger.info("   • Deleted indexer state")        
        await self.session.commit()
        logger.info("✅ Database reset complete")
    async def run(self) -> None:
        """Sync indexer to the current chain head."""
        # 1) Get latest indexed block from indexer state table
        proxy_start_block = 1
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
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as thread_ex:
                        fut = thread_ex.submit(
                            _fetch_block_data, current_block, self.subtensor
                        )
                        try:
                            results.append(
                                fut.result(
                                    timeout=settings.indexer_rpc_wait_timeout_seconds
                                )
                            )
                        except FuturesTimeoutError as exc:
                            logger.error(
                                f"❌ Timeout fetching block {current_block} after "
                                f"{settings.indexer_rpc_wait_timeout_seconds}s"
                            )
                            await self._save_indexer_state(
                                current_block - 1, status="error"
                            )
                            await self.session.commit()
                            raise RuntimeError(
                                f"RPC timeout on block {current_block}"
                            ) from exc
                else:
                    # Calculate batch range
                    batch_number = ((batch_start - proxy_start_block) // settings.batch_size) + 1
                    total_batches = ((end_block - proxy_start_block) // settings.batch_size) + 1
                    
                    logger.info(f"{'─'*80}")
                    logger.info(f"📦 Batch {batch_number}/{total_batches}: Processing blocks {batch_start} to {batch_end}")
                    logger.info(f"{'─'*80}")
                    
                    # Fetch blocks: on stall, discard partial progress and restart the whole batch
                    by_block: dict[int, tuple] = {}
                    stall_timeout = settings.indexer_rpc_wait_timeout_seconds
                    max_fetch_attempts = max(1, settings.indexer_rpc_fetch_retries)

                    for attempt in range(max_fetch_attempts):
                        executor = concurrent.futures.ProcessPoolExecutor(
                            max_workers=settings.max_workers
                        )
                        future_to_block = {
                            executor.submit(_fetch_block_data, bn): bn
                            for bn in range(batch_start, batch_end + 1)
                        }
                        pending = set(future_to_block.keys())
                        stalled = False
                        attempt_by_block: dict[int, tuple] = {}
                        try:
                            while pending:
                                done, _ = concurrent.futures.wait(
                                    pending,
                                    timeout=stall_timeout,
                                    return_when=concurrent.futures.FIRST_COMPLETED,
                                )
                                if not done:
                                    stalled = True
                                    pending_blocks = sorted(
                                        future_to_block[f] for f in pending
                                    )
                                    logger.warning(
                                        f"RPC stall: no block fetch completed in {stall_timeout}s "
                                        f"(attempt {attempt + 1}/{max_fetch_attempts}); "
                                        f"restarting full batch {batch_start}–{batch_end} "
                                        f"({len(pending_blocks)} were in flight; "
                                        f"e.g. {pending_blocks[:5]}{'...' if len(pending_blocks) > 5 else ''})"
                                    )
                                    break
                                for f in done:
                                    pending.remove(f)
                                    bn = future_to_block[f]
                                    try:
                                        attempt_by_block[bn] = f.result()
                                    except Exception as exc:
                                        logger.error(
                                            f"❌ Block {bn} generated an exception: {exc}"
                                        )
                                        await self._save_indexer_state(
                                            current_block - 1, status="error"
                                        )
                                        await self.session.commit()
                                        logger.error(
                                            f"❌ Batch processing failed. Indexer state saved at block {current_block - 1}"
                                        )
                                        raise RuntimeError(
                                            f"Batch processing failed at block {bn}"
                                        ) from exc
                        finally:
                            executor.shutdown(
                                wait=not pending, cancel_futures=True
                            )

                        if stalled:
                            if attempt >= max_fetch_attempts - 1:
                                await self._save_indexer_state(
                                    current_block - 1, status="error"
                                )
                                await self.session.commit()
                                raise RuntimeError(
                                    f"RPC stalled repeatedly on batch {batch_start}–{batch_end} "
                                    f"after {max_fetch_attempts} attempts"
                                )
                            time.sleep(min(2**attempt, 30))
                            continue

                        by_block = attempt_by_block
                        break

                    results = [by_block[bn] for bn in range(batch_start, batch_end + 1)]
                    
                batch_fetch_time = time.time() - batch_start_time
                
                # Process events for this batch
                if len(results) > 0:
                    logger.info(f"⚙️  Processing {len(results)} blocks with {sum(len(r[1]) for r in results)} events...")
                    process_start_time = time.time()
                    sorted_results = sorted(results, key=lambda x: x[0])
                    
                    try:
                        events_to_add = []
                        for block_number, events in sorted_results:
                            events_to_add.extend(events)

                        # Final state per (delegator, delegatee, proxy_type) so intra-batch
                        # add/remove order matches sequential processing before bulk IO.
                        final_proxy: dict[tuple[str, str, str], bool] = {}
                        for event in events_to_add:
                            key = (
                                event["delegator"],
                                event["delegatee"],
                                event["proxy_type"],
                            )
                            final_proxy[key] = not event["is_proxy_removed"]

                        remove_keys = [k for k, keep in final_proxy.items() if not keep]
                        if remove_keys:
                            logger.info(f"Removing {len(remove_keys)} proxies")
                            await self.session.execute(
                                delete(Proxy).where(
                                    tuple_(
                                        Proxy.delegator,
                                        Proxy.delegatee,
                                        Proxy.proxy_type,
                                    ).in_(remove_keys)
                                )
                            )

                        add_keys = [k for k, keep in final_proxy.items() if keep]
                        if add_keys:
                            logger.info(f"Adding {len(add_keys)} proxies")
                            self.session.add_all(
                                [
                                    Proxy(
                                        delegator=d,
                                        delegatee=e,
                                        proxy_type=t,
                                    )
                                    for d, e, t in add_keys
                                ]
                            )

                        await self.session.commit()
                    except Exception as exc:
                        logger.error(f'❌ Failed to process events batch: {exc}')
                        # Save error state and stop
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
    
    def stop(self):
        self.running = False
        
    def get_latest_block(self) -> int:
        """Get the latest block from the blockchain."""
        latest_block = self.subtensor.get_current_block()
        return latest_block

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
    if block_number % 1 == 0:
    # if block_number % 100 == 0:
        logger.info(f"Processing block {block_number}")
    block = subtensor.substrate.get_block_header(block_number=block_number)
    block_hash = block['header']['hash']
    events = subtensor.substrate.get_events(block_hash=block_hash)

    events_to_add = []
    for i in range(len(events)):
        event = events[i]
        event_id = event['event_id']
        if event_id == 'ProxyAdded':
            events_to_add.append({
                "is_proxy_removed": False,
                "delegator": event['event']['attributes']['delegator'],
                "delegatee": event['event']['attributes']['delegatee'],
                "proxy_type": event['event']['attributes']['proxy_type'],
            })
        if event_id == 'ProxyRemoved':
            events_to_add.append({
                "is_proxy_removed": True,
                "delegator": event['event']['attributes']['delegator'],
                "delegatee": event['event']['attributes']['delegatee'],
                "proxy_type": event['event']['attributes']['proxy_type'],
            })

    return (block_number, events_to_add)

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
