"""Subnets API endpoints — served from the snapshot cached by subnets-cacher."""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from src.common.cache import get_cache
from src.common.models import User
from src.api.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

REDIS_KEY_SNAPSHOT = "subnets:snapshot"
REDIS_CHANNEL = "subnets:stream"


async def _load_snapshot() -> dict:
    """Load and parse the latest subnets snapshot from Redis."""
    redis = await get_cache()
    raw = await redis.get(REDIS_KEY_SNAPSHOT)
    if raw is None:
        raise HTTPException(status_code=503, detail="Subnets snapshot not available yet")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"❌ Corrupt subnets snapshot in Redis: {e}")
        raise HTTPException(status_code=500, detail="Corrupt subnets snapshot")


@router.get("")
async def get_all_subnets(current_user: User = Depends(get_current_user)):
    """Return the latest cached snapshot of all subnets."""
    snapshot = await _load_snapshot()
    subnets = snapshot.get("subnets", {})
    return {
        "block_number": snapshot.get("block_number"),
        "cached_at": snapshot.get("cached_at"),
        "lock_cost": snapshot.get("lock_cost"),
        "total": len(subnets),
        "subnets": subnets,
    }


@router.get("/{netuid}")
async def get_subnet(netuid: int, current_user: User = Depends(get_current_user)):
    """Return a single subnet from the latest cached snapshot."""
    snapshot = await _load_snapshot()
    subnets = snapshot.get("subnets", {})
    # JSON keys are strings after deserialization
    subnet = subnets.get(str(netuid))
    if subnet is None:
        raise HTTPException(status_code=404, detail=f"Subnet {netuid} not found")
    return {
        "block_number": snapshot.get("block_number"),
        "cached_at": snapshot.get("cached_at"),
        "subnet": subnet,
    }


async def _read_snapshot(redis) -> Optional[dict]:
    """Read and parse the latest snapshot from Redis. Returns None if missing/corrupt."""
    raw = await redis.get(REDIS_KEY_SNAPSHOT)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"❌ Corrupt subnets snapshot in Redis: {e}")
        return None


async def _send_subnet_snapshot(websocket: WebSocket, redis, netuid: int) -> None:
    """Push this netuid's slice of the latest snapshot to the client."""
    snapshot = await _read_snapshot(redis)
    if snapshot is None:
        return
    subnet = snapshot.get("subnets", {}).get(str(netuid))
    if subnet is None:
        return
    await websocket.send_json({
        "type": "subnet_snapshot",
        "block_number": snapshot.get("block_number"),
        "cached_at": snapshot.get("cached_at"),
        "subnet": subnet,
    })


async def _send_all_subnets_snapshot(websocket: WebSocket, redis) -> None:
    """Push the full snapshot (all subnets) to the client."""
    snapshot = await _read_snapshot(redis)
    if snapshot is None:
        return
    subnets = snapshot.get("subnets", {})
    await websocket.send_json({
        "type": "all_subnets_snapshot",
        "block_number": snapshot.get("block_number"),
        "cached_at": snapshot.get("cached_at"),
        "lock_cost": snapshot.get("lock_cost"),
        "total": len(subnets),
        "subnets": subnets,
    })


@router.websocket("/ws")
async def all_subnets_websocket(websocket: WebSocket):
    """
    Real-time updates for the full subnets snapshot.

    Sends the entire snapshot on connect, then re-sends it every time
    subnets-cacher publishes to the `subnets:stream` channel.
    """
    await websocket.accept()
    redis = await get_cache()
    pubsub = None

    logger.info("✅ All-subnets WS connected")

    try:
        await _send_all_subnets_snapshot(websocket, redis)

        pubsub = redis.pubsub()
        await pubsub.subscribe(REDIS_CHANNEL)

        async def receive_messages():
            try:
                while True:
                    data = await websocket.receive_text()
                    if data == "ping":
                        await websocket.send_text("pong")
            except WebSocketDisconnect:
                pass

        async def send_updates():
            try:
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    await _send_all_subnets_snapshot(websocket, redis)
            except Exception as e:
                logger.error(f"All-subnets WS send_updates error: {e}")

        await asyncio.gather(
            receive_messages(),
            send_updates(),
            return_exceptions=True,
        )
    except WebSocketDisconnect:
        logger.info("❌ All-subnets WS disconnected")
    except Exception as e:
        logger.error(f"❌ All-subnets WS error: {e}", exc_info=True)
    finally:
        if pubsub is not None:
            try:
                await pubsub.unsubscribe(REDIS_CHANNEL)
                await pubsub.close()
            except Exception:
                pass


@router.websocket("/{netuid}/ws")
async def subnet_websocket(websocket: WebSocket, netuid: int):
    """
    Real-time subnet info updates.

    Sends the latest snapshot for this netuid on connect, then pushes a fresh
    one every time subnets-cacher publishes to the `subnets:stream` channel.
    """
    await websocket.accept()
    redis = await get_cache()
    pubsub = None

    logger.info(f"✅ Subnet WS connected for netuid={netuid}")

    try:
        # Initial snapshot (may be a no-op if cacher hasn't written yet)
        await _send_subnet_snapshot(websocket, redis, netuid)

        pubsub = redis.pubsub()
        await pubsub.subscribe(REDIS_CHANNEL)

        async def receive_messages():
            try:
                while True:
                    data = await websocket.receive_text()
                    if data == "ping":
                        await websocket.send_text("pong")
            except WebSocketDisconnect:
                pass

        async def send_updates():
            try:
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    await _send_subnet_snapshot(websocket, redis, netuid)
            except Exception as e:
                logger.error(f"Subnet WS send_updates error: {e}")

        await asyncio.gather(
            receive_messages(),
            send_updates(),
            return_exceptions=True,
        )
    except WebSocketDisconnect:
        logger.info(f"❌ Subnet WS disconnected for netuid={netuid}")
    except Exception as e:
        logger.error(f"❌ Subnet WS error for netuid={netuid}: {e}", exc_info=True)
    finally:
        if pubsub is not None:
            try:
                await pubsub.unsubscribe(REDIS_CHANNEL)
                await pubsub.close()
            except Exception:
                pass
