"""Events API endpoints for historical and real-time trade data."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.db import get_session
from src.common.cache import get_cache
from src.common.config import settings
from src.common.models import FailedTrade, Trade, Transfer, User
from src.api.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/history")
async def get_events_history(
    limit: int = 50,
    netuid: Optional[int] = None,
    from_timestamp: Optional[int] = None,
    to_timestamp: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Get historical trade and transfer events from the last N blocks or time range.
    
    Args:
        limit: Number of most recent blocks to fetch (default: 50, max: 200) - ignored if timestamps provided
        netuid: Optional subnet ID filter
        from_timestamp: Unix timestamp - fetch events from this time onwards
        to_timestamp: Unix timestamp - fetch events up to this time
    
    Returns:
        List of trade and transfer events grouped by block
    """
    # Validate limit
    if limit < 1:
        raise HTTPException(status_code=400, detail="Limit must be at least 1")
    if limit > 200:
        raise HTTPException(status_code=400, detail="Limit cannot exceed 200")
    
    try:
        # Determine query mode: time-based or block-based
        use_time_query = from_timestamp is not None or to_timestamp is not None
        
        if use_time_query:
            # Time-based query
            trade_query = select(Trade).where(
                Trade.is_coldkey_swapped == False,
            )
            
            if from_timestamp is not None:
                from_dt = datetime.fromtimestamp(from_timestamp)
                trade_query = trade_query.where(Trade.block_timestamp >= from_dt)
            
            if to_timestamp is not None:
                to_dt = datetime.fromtimestamp(to_timestamp)
                trade_query = trade_query.where(Trade.block_timestamp <= to_dt)
            
            trade_query = trade_query.order_by(Trade.block_number.desc(), Trade.id.desc())
            
            # Query transfers with same time range
            transfer_query = select(Transfer).where(
                Transfer.is_coldkey_swap == False,
            )
            
            if from_timestamp is not None:
                transfer_query = transfer_query.where(Transfer.block_timestamp >= from_dt)
            
            if to_timestamp is not None:
                transfer_query = transfer_query.where(Transfer.block_timestamp <= to_dt)
            
            transfer_query = transfer_query.order_by(Transfer.block_number.desc(), Transfer.id.desc())
            
        else:
            # Block-based query (original logic)
            # Get the latest block number from both trades and transfers
            latest_trade_query = select(func.max(Trade.block_number))
            result = await session.execute(latest_trade_query)
            latest_trade_block = result.scalar() or 0
            
            latest_transfer_query = select(func.max(Transfer.block_number))
            result = await session.execute(latest_transfer_query)
            latest_transfer_block = result.scalar() or 0
            
            latest_block = max(latest_trade_block, latest_transfer_block)
            
            if latest_block == 0:
                return {
                    "latest_block": 0,
                    "trade_blocks": [],
                    "transfer_blocks": []
                }
            
            # Calculate the starting block
            start_block = max(0, latest_block - limit + 1)
            
            # Query trades from the last N blocks
            trade_query = select(Trade).where(
                Trade.block_number >= start_block,
                Trade.is_coldkey_swapped == False,
            ).order_by(Trade.block_number.desc(), Trade.id.desc())
            
            # Query transfers from the last N blocks  
            transfer_query = select(Transfer).where(
                Transfer.block_number >= start_block,
                Transfer.is_coldkey_swap == False,
            ).order_by(Transfer.block_number.desc(), Transfer.id.desc())
        
        # Add netuid filter if specified (for both modes)
        if netuid is not None:
            trade_query = trade_query.where(Trade.netuid == netuid)
            transfer_query = transfer_query.where(Transfer.netuid == netuid)
        
        # Execute queries
        result = await session.execute(trade_query)
        trades = result.scalars().all()
        
        result = await session.execute(transfer_query)
        transfers = result.scalars().all()
        
        # Group trades by block
        trade_blocks_dict = {}
        for trade in trades:
            block_num = trade.block_number
            if block_num not in trade_blocks_dict:
                trade_blocks_dict[block_num] = {
                    'block_number': block_num,
                    'block_timestamp': int(trade.block_timestamp.timestamp()),
                    'events': []
                }
            
            trade_blocks_dict[block_num]['events'].append({
                'id': trade.id,
                'event_id': 'StakeRemoved' if trade.is_sell else 'StakeAdded',
                'is_sell': trade.is_sell,
                'netuid': trade.netuid,
                'tao': trade.tao,
                'alpha': trade.alpha,
                'price': trade.price,
                'address': trade.address,
                'block_hash': trade.block_hash,
                'extrinsic_index': trade.extrinsic_index,
                'is_transferred': trade.is_transferred,
                'is_moved': trade.is_moved,
                'is_pure_move': trade.is_pure_move,
            })
        
        # Group transfers by block
        transfer_blocks_dict = {}
        for transfer in transfers:
            block_num = transfer.block_number
            if block_num not in transfer_blocks_dict:
                transfer_blocks_dict[block_num] = {
                    'block_number': block_num,
                    'block_timestamp': int(transfer.block_timestamp.timestamp()),
                    'events': []
                }
            
            transfer_blocks_dict[block_num]['events'].append({
                'id': transfer.id,
                'event_id': 'Transfer' if transfer.netuid == -1 else 'StakeTransferred',
                'netuid': transfer.netuid,
                'from_address': transfer.from_address,
                'to_address': transfer.to_address,
                'tao_amount': transfer.tao_amount,
                'block_hash': transfer.block_hash,
                'extrinsic_index': transfer.extrinsic_index,
            })
        
        # Convert to sorted lists
        trade_blocks = sorted(trade_blocks_dict.values(), key=lambda x: x['block_number'], reverse=True)
        transfer_blocks = sorted(transfer_blocks_dict.values(), key=lambda x: x['block_number'], reverse=True)
        
        if use_time_query:
            logger.info(f"📊 Fetched {len(trades)} trade events from {len(trade_blocks)} blocks and {len(transfers)} transfer events from {len(transfer_blocks)} blocks (time range: {from_timestamp} to {to_timestamp})")
        else:
            logger.info(f"📊 Fetched {len(trades)} trade events from {len(trade_blocks)} blocks and {len(transfers)} transfer events from {len(transfer_blocks)} blocks (starting from block {start_block})")
        
        response_data = {
            "total_trade_blocks": len(trade_blocks),
            "total_trade_events": len(trades),
            "total_transfer_blocks": len(transfer_blocks),
            "total_transfer_events": len(transfers),
            "trade_blocks": trade_blocks,
            "transfer_blocks": transfer_blocks,
            "netuid_filter": netuid
        }
        
        # Add query mode specific fields
        if use_time_query:
            response_data["from_timestamp"] = from_timestamp
            response_data["to_timestamp"] = to_timestamp
        else:
            response_data["latest_block"] = latest_block
            response_data["start_block"] = start_block
        
        return response_data
        
    except Exception as e:
        logger.error(f"❌ Error fetching events history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/failed")
async def get_failed_trades(
    from_timestamp: Optional[int] = None,
    to_timestamp: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get failed trade extrinsics within a time window.

    Args:
        from_timestamp: Unix timestamp - fetch records from this time onwards
        to_timestamp: Unix timestamp - fetch records up to this time
    """
    try:
        query = select(FailedTrade)

        if from_timestamp is not None:
            query = query.where(
                FailedTrade.block_timestamp >= datetime.fromtimestamp(from_timestamp)
            )
        if to_timestamp is not None:
            query = query.where(
                FailedTrade.block_timestamp <= datetime.fromtimestamp(to_timestamp)
            )

        query = query.order_by(FailedTrade.block_number.desc(), FailedTrade.id.desc())

        result = await session.execute(query)
        failed_trades = result.scalars().all()

        return {
            "failed_trades": [
                {
                    "id": ft.id,
                    "block_number": ft.block_number,
                    "block_hash": ft.block_hash,
                    "block_timestamp": int(ft.block_timestamp.timestamp()),
                    "extrinsic_index": ft.extrinsic_index,
                    "error_message": ft.error_message,
                    "address": ft.address,
                    "proxy_address": ft.proxy_address,
                    "call_module": ft.call_module,
                    "call_function": ft.call_function,
                    "call_args": ft.call_args,
                }
                for ft in failed_trades
            ],
            "from_timestamp": from_timestamp,
            "to_timestamp": to_timestamp,
            "total": len(failed_trades),
        }
    except Exception as e:
        logger.error(f"❌ Error fetching failed trades: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


def _get_ws_user_email(websocket: WebSocket) -> Optional[str]:
    """Get user email from JWT in query param ?token=... (optional)."""
    token = websocket.query_params.get("token")
    if not token:
        return None
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        return payload.get("sub")
    except JWTError:
        return None


# Pending call types only shown to mempool_admin_emails
MEMPOOL_RESTRICTED_DISPLAY_TYPES = frozenset({"evm"})
# MEMPOOL_RESTRICTED_DISPLAY_TYPES = frozenset({"evm", "mevshield"})


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time event updates.
    
    Clients will receive:
    1. Initial data: cached events from last 3 blocks
    2. Real-time updates: new events as they happen via Redis pub/sub
    
    Optional query param: token=JWT to identify user. EVM and MevShield pending
    extrinsics are only sent when the token's email is in mempool_admin_emails.
    """
    await websocket.accept()
    redis = await get_cache()
    user_email = _get_ws_user_email(websocket)
    admin_emails = {e.strip().lower() for e in settings.mempool_admin_emails.split(",") if e.strip()}
    can_see_restricted = user_email and user_email.lower() in admin_emails

    logger.info(f"✅ WebSocket client connected (user={user_email or 'anonymous'})")
    
    try:
        # Send cached trade events from last 3 blocks
        cached_trade_blocks = await redis.zrange('events:blocks', 0, -1)
        trade_blocks = []
        for block_num in cached_trade_blocks:
            redis_key = f"events:block:{block_num}"
            data = await redis.get(redis_key)
            if data:
                trade_blocks.append(json.loads(data))
        
        # Send cached transfer events from last 3 blocks
        cached_transfer_blocks = await redis.zrange('transfers:blocks', 0, -1)
        transfer_blocks = []
        for block_num in cached_transfer_blocks:
            redis_key = f"transfers:block:{block_num}"
            data = await redis.get(redis_key)
            if data:
                transfer_blocks.append(json.loads(data))
        
        if trade_blocks or transfer_blocks:
            await websocket.send_json({
                'type': 'initial_data',
                'trade_blocks': [{'block_number': block['block_number'], 'events': block['events']} for block in trade_blocks],
                'transfer_blocks': [{'block_number': block['block_number'], 'events': block['events']} for block in transfer_blocks],
                'timestamp': int(datetime.utcnow().timestamp())
            })
            logger.info(f"📤 Sent initial data ({len(trade_blocks)} trade blocks, {len(transfer_blocks)} transfer blocks)")
        
        # Subscribe to Redis pub/sub for real-time updates (events, transfers, mempool pending)
        pubsub = redis.pubsub()
        await pubsub.subscribe('events:stream', 'transfers:stream', 'mempool:stream', 'blocks:stream', 'failed_trades:stream')
        
        # Create tasks for sending and receiving
        async def receive_messages():
            """Receive ping/pong from client"""
            try:
                while True:
                    data = await websocket.receive_text()
                    if data == "ping":
                        await websocket.send_text("pong")
            except WebSocketDisconnect:
                pass
        
        async def send_updates():
            """Send Redis pub/sub updates to client. Filter EVM/MevShield pending to admin only."""
            try:
                async for message in pubsub.listen():
                    if message['type'] != 'message':
                        continue
                    data = message['data']
                    if not can_see_restricted:
                        try:
                            obj = json.loads(data)
                            if isinstance(obj, dict) and obj.get("type") == "pending_extrinsic":
                                if obj.get("display_type") in MEMPOOL_RESTRICTED_DISPLAY_TYPES:
                                    continue
                        except (json.JSONDecodeError, TypeError):
                            pass
                    await websocket.send_text(data)
            except Exception as e:
                logger.error(f"Error in send_updates: {e}")
        
        # Run both tasks concurrently
        await asyncio.gather(
            receive_messages(),
            send_updates(),
            return_exceptions=True
        )
        
    except WebSocketDisconnect:
        logger.info("❌ WebSocket client disconnected")
    except Exception as e:
        logger.error(f"❌ WebSocket error: {e}", exc_info=True)
    finally:
        try:
            await pubsub.unsubscribe('events:stream', 'transfers:stream', 'mempool:stream', 'blocks:stream', 'failed_trades:stream')
            await pubsub.close()
        except:
            pass
        logger.info("WebSocket connection closed")

