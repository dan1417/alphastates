"""Blockchain data processing logic for Transfer indexer."""

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

from src.common.config import settings
from src.common.models import Transfer, IndexerState
from src.common.utils import is_special_address

logger = logging.getLogger(__name__)

_process_subtensor = None

class TransferProcessor:
    """Process and index transfer events from Bittensor network."""

    INDEXER_NAME = "transfer"

    def __init__(self, session: AsyncSession):
        self.session = session
        self.subtensor = None
        self.running = False

    async def initialize(self) -> None:
        """Initialize processor dependencies."""
        self.subtensor = bt.Subtensor(settings.rpc_url)

        logger.info(f"Using {settings.max_workers} workers")
        logger.info(f"Using {settings.batch_size} blocks per batch")
        logger.info(f"Using 1 as start block")
        logger.info(f"Using {settings.rpc_url} as RPC URL")
        logger.info(f"Using {settings.database_url} as Database URL")

    async def _load_indexer_state(self) -> int:
        """
        Load the last indexed block from the indexer state table.
        
        Returns:
            The last indexed block number, or transfer_start_block if no state exists.
        """
        transfer_start_block = 1

        stmt = select(IndexerState).where(IndexerState.indexer_name == self.INDEXER_NAME)
        result = await self.session.execute(stmt)
        state = result.scalar_one_or_none()
        
        if state is None:
            logger.info(f"No previous state found, starting from block {transfer_start_block}")
            return transfer_start_block
        
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
        """Reset the database by deleting all rows from all tables."""
        logger.warning("🔄 RESET_DB is enabled - deleting all data from database...")
        
        # Delete all transfers
        stmt = delete(Transfer)
        await self.session.execute(stmt)
        logger.info("   • Deleted all transfers")
        
        # Delete indexer state
        stmt = delete(IndexerState).where(IndexerState.indexer_name == self.INDEXER_NAME)
        await self.session.execute(stmt)
        logger.info("   • Deleted indexer state")
        
        # Commit the deletions
        await self.session.commit()
        
        logger.info("✅ Database reset complete")

    async def run(self) -> None:
        """Sync indexer to the current chain head."""
        # 1) Get latest indexed block from indexer state table
        transfer_start_block = 1
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
                    batch_number = ((batch_start - transfer_start_block) // settings.batch_size) + 1
                    total_batches = ((end_block - transfer_start_block) // settings.batch_size) + 1
                    
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
                
                # Process events for this batch
                if len(results) > 0:
                    logger.info(f"⚙️  Processing {len(results)} blocks with {sum(len(r[1]) for r in results)} events...")
                    process_start_time = time.time()
                    sorted_results = sorted(results, key=lambda x: x[0])
                    
                    try:
                        rows_to_add = []
                        for block_number, events in sorted_results:
                            rows_to_add.extend(events)

                        self.session.add_all(rows_to_add)
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
    if block_number % 100 == 0:
        logger.info(f"Processing block {block_number}")
    block = subtensor.substrate.get_block_header(block_number=block_number)
    block_hash = block['header']['hash']
    events = subtensor.substrate.get_events(block_hash=block_hash)
    timestamp = round(subtensor.get_timestamp(block_number).timestamp(), 0)

    subnet_prices = None
    if block_number >= settings.dtao_start_block:
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

    events_to_add = []
    for i in range(len(events)):
        event = events[i]
        event_id = event['event_id']
        if event_id == 'Transfer':
            from_address = event['event']['attributes']['from']
            to_address = event['event']['attributes']['to']
            if is_special_address(from_address) or is_special_address(to_address):
                continue
            amount = event['event']['attributes']['amount']
            events_to_add.append({
                "netuid": -1,
                "from_address": from_address,
                "to_address": to_address,
                "tao_amount": amount,
                'extrinsic_index': event['extrinsic_idx'],
                'is_coldkey_swap': False,
            })
        if event_id == 'StakeTransferred':
            from_address = event['event']['attributes'][0]
            to_address = event['event']['attributes'][1]
            if is_special_address(from_address) or is_special_address(to_address):
                continue
            hotkey_address = event['event']['attributes'][2]
            origin_netuid = event['event']['attributes'][3]
            dest_netuid = event['event']['attributes'][4]
            amount = event['event']['attributes'][5]
            if origin_netuid == dest_netuid:
                events_to_add.append({
                    "netuid": dest_netuid,
                    "from_address": from_address,
                    "to_address": to_address,
                    "tao_amount": amount,
                    'extrinsic_index': event['extrinsic_idx'],
                    'is_coldkey_swap': False,
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

            logger.info(f"Old address type: {type(old_address)}")
            logger.info(f"New address type: {type(new_address)}")
            
            balance = subtensor.get_balance(address=new_address, block=block_number)
            events_to_add.append({
                "netuid": -1,
                "from_address": old_address,
                "to_address": new_address,
                "tao_amount": balance.rao,
                'extrinsic_index': -1 if event['extrinsic_idx'] is None else event['extrinsic_idx'],
                'is_coldkey_swap': True,
            })

            if block_number >= settings.dtao_start_block:
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

                for netuid, alpha in subnet_alpha.items():
                    events_to_add.append({
                        'netuid': netuid,
                        'from_address': old_address,
                        'to_address': new_address,
                        'tao_amount': subnet_prices[netuid] / 10**9 * alpha,
                        'extrinsic_index': -1 if event['extrinsic_idx'] is None else event['extrinsic_idx'],
                        'is_coldkey_swap': True,
                    })

    events = [Transfer(
        block_number=block_number,
        block_hash=block_hash,
        block_timestamp=datetime.fromtimestamp(timestamp),
        extrinsic_index=event['extrinsic_index'],
        netuid=event['netuid'],
        from_address=event['from_address'],
        to_address=event['to_address'],
        tao_amount=event['tao_amount'],
        is_coldkey_swap=event['is_coldkey_swap'],
    ) for event in events_to_add]

    return (block_number, events)

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
