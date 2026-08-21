"""OHLC data API routes."""

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import get_current_user
from src.common.cache import get_cache
from src.common.db import get_session
from src.common.models import OHLC_MODELS, OHLCInterval, User

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/candles")
async def get_candles(
    netuid: int,
    interval: str,
    start_time: int,
    end_time: int,
    limit: int = 5000,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Get OHLC candles for a specific netuid and interval.
    
    - If both start_time and end_time: return range [start, end]
    - If neither: return last N candles
    """
    # Validate interval
    try:
        interval_enum = OHLCInterval(interval)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid interval: {interval}")
    
    # Get model for this interval
    model = OHLC_MODELS[interval_enum]
    
    # Build query
    query = select(model).where(model.netuid == netuid)

    if start_time:
        query = query.where(model.open_time >= datetime.fromtimestamp(start_time))
    
    if end_time:
        query = query.where(model.open_time <= datetime.fromtimestamp(end_time))
    
    # Order and limit
    query = query.order_by(model.open_time.asc()).limit(min(limit, 5000))
    
    # Execute
    result = await session.execute(query)
    candles = result.scalars().all()
    
    # Format response from database
    candle_list = [
        {
            "time": int(candle.open_time.timestamp()),
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume_tao
        }
        for candle in candles
    ]
    
    # Try to get cached latest candle from Redis and append it
    try:
        cache = await get_cache()
        redis_key = f"ohlc_{interval}_last_indexed_candle"
        cached_data = await cache.get(redis_key)
        
        if cached_data:
            cached_candle = json.loads(cached_data)
            
            # Check if this netuid has data in the cached candle
            if "prices" in cached_candle and str(netuid) in cached_candle["prices"]:
                netuid_data = cached_candle["prices"][str(netuid)]
                cached_time = cached_candle["open_time"]
                
                # Only append if it's newer than the last DB candle
                if not candle_list or cached_time > candle_list[-1]["time"]:
                    cached_candle_entry = {
                        "time": cached_time,
                        "open": netuid_data["open"],
                        "high": netuid_data["high"],
                        "low": netuid_data["low"],
                        "close": netuid_data["close"],
                        "volume": netuid_data["volume_tao"]
                    }
                    candle_list.append(cached_candle_entry)
                    logger.debug(f"Appended cached candle for netuid {netuid}, interval {interval}")
    except Exception as e:
        # Don't fail the request if cache is unavailable, just log it
        logger.warning(f"Could not fetch cached candle: {e}")
    
    return {
        "netuid": netuid,
        "interval": interval,
        "candles": candle_list
    }
