"""API routes for stake information."""

import asyncio
import logging
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException

from src.api.auth import get_current_user
from src.common.subtensor_pool import get_subtensor_pool

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/coldkey/{coldkey_address}")
async def get_stake_info(
    coldkey_address: str,
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """Get stake information for a coldkey address.
    
    Args:
        coldkey_address: The coldkey SS58 address to query
        current_user: Authenticated user (from JWT token)
        
    Returns:
        Dictionary containing stake information for the coldkey
        
    Raises:
        HTTPException: If the query fails
    """
    try:
        pool = get_subtensor_pool()
        
        # Get a connection from the pool
        async with pool.get_connection() as subtensor:
            # Run the blocking query in a thread pool
            loop = asyncio.get_event_loop()
            stake_info = await loop.run_in_executor(
                None,
                subtensor.get_stake_info_for_coldkey,
                coldkey_address
            )
            subnet_prices = await loop.run_in_executor(
                None,
                subtensor.get_subnet_prices
            )
            free_tao = await loop.run_in_executor(
                None,
                subtensor.get_balance,
                coldkey_address
            )
        
        # Convert stake info to serializable format
        stakes = []
        total_staked = 0.0
        
        for stake in stake_info:
            stake_alpha = float(stake.stake.tao)
            stake_tao = subnet_prices[stake.netuid].tao * stake_alpha
            total_staked += stake_tao
            
            stakes.append({
                "hotkey_ss58": stake.hotkey_ss58,
                "coldkey_ss58": stake.coldkey_ss58,
                "netuid": stake.netuid,
                "stake_alpha": stake_alpha,
                "stake_tao": stake_tao
            })
        
        return {
            "coldkey_address": coldkey_address,
            "free_tao": free_tao.tao,
            "total_staked_tao": total_staked,
            "stakes": stakes,
            "stake_count": len(stakes)
        }
        
    except Exception as e:
        logger.error(f"Error getting stake info for {coldkey_address}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get stake info: {str(e)}"
        )


@router.get("/pool/status")
async def get_pool_status(
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """Get the status of the subtensor connection pool.
    
    Args:
        current_user: Authenticated user (from JWT token)
        
    Returns:
        Dictionary containing pool status information
    """
    try:
        pool = get_subtensor_pool()
        
        return {
            "initialized": pool.is_initialized(),
            "pool_size": pool.pool_size,
            "available_connections": pool.available_connections(),
            "in_use_connections": pool.pool_size - pool.available_connections()
        }
        
    except Exception as e:
        logger.error(f"Error getting pool status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get pool status: {str(e)}"
        )
