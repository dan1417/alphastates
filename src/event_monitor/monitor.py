"""WebSocket monitor for real-time blockchain events."""

import asyncio
import json
import logging
from typing import Set, Dict, Any, List
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import bittensor as bt
from bittensor.core.subtensor import Subtensor
from redis.asyncio import Redis

from src.common.config import settings
from src.common.utils import is_special_address

logger = logging.getLogger(__name__)


class EventMonitor:
    """Monitor blockchain for StakeAdded, StakeRemoved, Transfer, and StakeTransferred events."""
    
    def __init__(self, redis_client: Redis):
        self.redis = redis_client
        self.subtensor: Subtensor | None = None
        self.last_processed_block = 0
        self.running = False
        self.loop = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        
    async def start(self):
        """Start monitoring blockchain events."""
        self.running = True
        self.loop = asyncio.get_event_loop()
        logger.info("🚀 Starting WebSocket event monitor...")
        
        # Run the blocking monitor in a separate thread
        await self.loop.run_in_executor(self.executor, self._monitor_thread)
    
    async def stop(self):
        """Stop monitoring."""
        self.running = False
        self.executor.shutdown(wait=False)
        logger.info("🛑 Stopping WebSocket event monitor...")
    
    def _monitor_thread(self):
        """Monitor thread that runs blocking operations."""
        try:
            # Initialize subtensor in the thread
            self.subtensor = bt.Subtensor(network=settings.rpc_url)
            logger.info(f"✅ Connected to Bittensor network: {settings.rpc_url}")
        except Exception as e:
            logger.error(f"❌ Failed to connect to Bittensor: {e}")
            return
        
        # Get current block number
        current_block = self.subtensor.get_current_block()
        logger.info(f"📊 Starting from block: {current_block}")
        
        # Start monitoring loop
        while self.running:
            try:
                # Process the block (schedule async work)
                future = asyncio.run_coroutine_threadsafe(
                    self._process_block(current_block),
                    self.loop
                )
                future.result()  # Wait for async processing to complete
                
                current_block += 1
                logger.info(f"🔍 Waiting for block: {current_block}")
                
                # This is the blocking call - runs in thread
                self.subtensor.wait_for_block(current_block)
                
            except Exception as e:
                logger.error(f"❌ Error in monitor thread: {e}", exc_info=True)
                import time
                time.sleep(12)  # Use time.sleep in thread, not asyncio.sleep
    
    async def _process_block(self, block_number: int):
        """Process a single block and extract events."""
        try:
            logger.info(f"🔍 Processing block {block_number}...")
            
            # Get block data
            block = self.subtensor.substrate.get_block_header(block_number=block_number)
            block_hash = block['header']['hash']
            events = self.subtensor.substrate.get_events(block_hash=block_hash)
            timestamp = self.subtensor.get_timestamp(block_number).timestamp()
            
            # Extract StakeAdded and StakeRemoved events
            filtered_trade_events = []
            filtered_transfer_events = []
            num_events = len(events)
            
            for i, event in enumerate(events):
                event_id = event['event']['event_id']
                
                # Handle Trade events (StakeAdded/StakeRemoved)
                if event_id in ['StakeAdded', 'StakeRemoved']:
                    # Skip zero amount events
                    if event['event']['attributes'][2] == 0 or event['event']['attributes'][3] == 0:
                        continue
                    
                    # Check for transfer/move flags
                    is_transferred = False
                    is_moved = False
                    is_pure_move = False
                    
                    # if block_number < 7782730: # StakeRemoved -> StakeAdded -> StakeMoved/StakeTransferred    
                    #     if event_id == 'StakeRemoved' and i + 2 < num_events:
                    #         if events[i + 2]['event']['event_id'] == 'StakeTransferred':
                    #             is_transferred = True
                    #         if events[i + 2]['event']['event_id'] == 'StakeMoved':
                    #             is_moved = True
                    #             if events[i]['event']['attributes'][4] == events[i + 1]['event']['attributes'][4]:
                    #                 is_pure_move = True
                    #     if event_id == 'StakeAdded' and i + 1 < num_events:
                    #         if events[i + 1]['event']['event_id'] == 'StakeTransferred':
                    #             is_transferred = True
                    #         if events[i + 1]['event']['event_id'] == 'StakeMoved':
                    #             is_moved = True
                    #             if events[i]['event']['attributes'][4] == events[i - 1]['event']['attributes'][4]:
                    #                 is_pure_move = True
                    # else: # StakeRemoved -> Deposit -> StakeAdded -> StakeMoved/StakeTransferred    
                    #     if event_id == 'StakeRemoved' and i + 3 < num_events:
                    #         if events[i + 3]['event']['event_id'] == 'StakeTransferred':
                    #             is_transferred = True
                    #         if events[i + 3]['event']['event_id'] == 'StakeMoved':
                    #             is_moved = True
                    #             if events[i]['event']['attributes'][4] == events[i + 2]['event']['attributes'][4]:
                    #                 is_pure_move = True
                    #     if event_id == 'StakeAdded' and i + 1 < num_events:
                    #         if events[i + 1]['event']['event_id'] == 'StakeTransferred':
                    #             is_transferred = True
                    #         if events[i + 1]['event']['event_id'] == 'StakeMoved':
                    #             is_moved = True
                    #             if events[i]['event']['attributes'][4] == events[i - 2]['event']['attributes'][4]:
                    #                 is_pure_move = True

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
                            
                    # Calculate price (avoiding division by zero)
                    tao_amount = event['event']['attributes'][2]
                    alpha_amount = event['event']['attributes'][3]
                    price = round(10**9 * tao_amount / alpha_amount, 0) if alpha_amount > 0 else 0
                    
                    event_data = {
                        'event_id': event_id,
                        'is_sell': event_id == 'StakeRemoved',
                        'netuid': event['event']['attributes'][4],
                        'tao': tao_amount,
                        'alpha': alpha_amount,
                        'price': price,
                        'address': event['event']['attributes'][0],
                        'block_number': block_number,
                        'block_timestamp': int(timestamp),
                        'block_hash': block_hash,
                        'extrinsic_index': event['extrinsic_idx'] if event['extrinsic_idx'] is not None else -1,
                        'is_transferred': is_transferred,
                        'is_moved': is_moved,
                        'is_pure_move': is_pure_move,
                    }
                    
                    filtered_trade_events.append(event_data)
                
                # Handle Transfer events
                elif event_id == 'Transfer':
                    from_address = event['event']['attributes']['from']
                    to_address = event['event']['attributes']['to']
                    if is_special_address(from_address) or is_special_address(to_address):
                        continue
                    amount = event['event']['attributes']['amount']
                    transfer_data = {
                        'event_id': event_id,
                        'netuid': -1,
                        'from_address': from_address,
                        'to_address': to_address,
                        'tao_amount': amount,
                        'block_number': block_number,
                        'block_timestamp': int(timestamp),
                        'block_hash': block_hash,
                        'extrinsic_index': event['extrinsic_idx'] if event['extrinsic_idx'] is not None else -1,
                    }
                    
                    filtered_transfer_events.append(transfer_data)
                
                # Handle StakeTransferred events
                elif event_id == 'StakeTransferred':
                    from_address = event['event']['attributes'][0]
                    to_address = event['event']['attributes'][1]
                    if is_special_address(from_address) or is_special_address(to_address):
                        continue
                    hotkey_address = event['event']['attributes'][2]
                    origin_netuid = event['event']['attributes'][3]
                    dest_netuid = event['event']['attributes'][4]
                    amount = event['event']['attributes'][5]
                    
                    # Only log if same netuid (actual transfer, not move between subnets)
                    if origin_netuid == dest_netuid:
                        transfer_data = {
                            'event_id': event_id,
                            'netuid': dest_netuid,
                            'from_address': from_address,
                            'to_address': to_address,
                            'tao_amount': amount,
                            'block_number': block_number,
                            'block_timestamp': int(timestamp),
                            'block_hash': block_hash,
                            'extrinsic_index': event['extrinsic_idx'] if event['extrinsic_idx'] is not None else -1,
                        }
                        
                        filtered_transfer_events.append(transfer_data)
            
            # Handle trade events
            if filtered_trade_events:
                logger.info(f"✅ Found {len(filtered_trade_events)} trade events in block {block_number}")
                filtered_trade_events.reverse()
                
                # Store block events in Redis (keep last 3 blocks)
                await self._cache_block_events(block_number, filtered_trade_events, 'events')
                
                # Publish to Redis channel for WebSocket clients
                await self._publish_events(block_number, filtered_trade_events, 'events')
            
            # Handle transfer events
            if filtered_transfer_events:
                logger.info(f"✅ Found {len(filtered_transfer_events)} transfer events in block {block_number}")
                filtered_transfer_events.reverse()
                
                # Store block events in Redis (keep last 3 blocks)
                await self._cache_block_events(block_number, filtered_transfer_events, 'transfers')
                
                # Publish to Redis channel for WebSocket clients
                await self._publish_events(block_number, filtered_transfer_events, 'transfers')
            
            if not filtered_trade_events and not filtered_transfer_events:
                logger.debug(f"No events found in block {block_number}")

            # Always publish new_block so frontend can show last block and time since
            await self._publish_new_block(block_number)

        except Exception as e:
            logger.error(f"❌ Error processing block {block_number}: {e}", exc_info=True)
    
    async def _cache_block_events(self, block_number: int, events: List[Dict[str, Any]], event_type: str = 'events'):
        """Cache block events in Redis (keep last 3 blocks)."""
        try:
            # Store current block events
            redis_key = f"{event_type}:block:{block_number}"
            await self.redis.set(
                redis_key,
                json.dumps({
                    'block_number': block_number,
                    'events': events,
                    'cached_at': int(datetime.utcnow().timestamp())
                }),
                ex=3600  # Expire after 1 hour
            )
            
            # Keep track of cached blocks
            await self.redis.zadd(
                f'{event_type}:blocks',
                {str(block_number): block_number}
            )
            
            # Remove old blocks (keep only last 3)
            cached_blocks = await self.redis.zrange(f'{event_type}:blocks', 0, -1)
            if len(cached_blocks) > 3:
                blocks_to_remove = cached_blocks[:-3]
                for old_block in blocks_to_remove:
                    await self.redis.delete(f"{event_type}:block:{old_block}")
                    await self.redis.zrem(f'{event_type}:blocks', old_block)
                
                logger.info(f"🗑️ Removed {len(blocks_to_remove)} old cached {event_type} blocks")
            
            logger.debug(f"💾 Cached {len(events)} {event_type} for block {block_number}")
            
        except Exception as e:
            logger.error(f"❌ Failed to cache block {event_type}: {e}", exc_info=True)
    
    async def _publish_events(self, block_number: int, events: List[Dict[str, Any]], event_type: str = 'events'):
        """Publish events to Redis pub/sub channel."""
        try:
            message = {
                'type': 'new_events' if event_type == 'events' else 'new_transfers',
                'block_number': block_number,
                'events': events,
                'timestamp': int(datetime.utcnow().timestamp())
            }
            
            await self.redis.publish(f'{event_type}:stream', json.dumps(message))
            logger.info(f"📡 Published {len(events)} {event_type} for block {block_number}")
            
        except Exception as e:
            logger.error(f"❌ Failed to publish {event_type}: {e}", exc_info=True)

    async def _publish_new_block(self, block_number: int):
        """Publish new block to Redis so frontend can show last block and time since."""
        try:
            message = {'type': 'new_block', 'block_number': block_number}
            await self.redis.publish('blocks:stream', json.dumps(message))
            logger.debug(f"📡 Published new_block {block_number}")
        except Exception as e:
            logger.error(f"❌ Failed to publish new_block: {e}", exc_info=True)
    
    async def get_cached_blocks(self) -> List[Dict[str, Any]]:
        """Get cached events from last 3 blocks."""
        try:
            # Get list of cached blocks (sorted by block number)
            cached_blocks = await self.redis.zrange('events:blocks', 0, -1)
            
            result = []
            for block_num in cached_blocks:
                redis_key = f"events:block:{block_num}"
                data = await self.redis.get(redis_key)
                if data:
                    result.append(json.loads(data))
            
            logger.info(f"📦 Retrieved {len(result)} cached blocks")
            return result
            
        except Exception as e:
            logger.error(f"❌ Failed to get cached blocks: {e}", exc_info=True)
            return []

