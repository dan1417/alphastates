"""API routes for address data."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import bittensor as bt

from src.api.auth import get_current_user
from src.common.db import get_session
from src.common.config import settings
from src.common.models import User

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/default")
async def get_default_addresses(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Get all subnet owner addresses with their types.
    
    Returns:
        {
            "addresses": {address: [label, type]},
            "types": {type_name: color}
        }
    """
    try:
        subtensor = bt.Subtensor(settings.rpc_url)
        owners = {subnet.netuid: subnet.owner_coldkey for subnet in subtensor.all_subnets()}

        # Use the project's redis cache abstraction
        from src.common.cache import get_cache

        cache = await get_cache()
        
        # Get default addresses
        raw_addresses = await cache.get('default_addresses')
        if raw_addresses is None:
            raise HTTPException(status_code=404, detail="Default addresses not found in redis")
        import json
        default_addresses = json.loads(raw_addresses)
        
        # Get default types
        raw_types = await cache.get('default_types')
        default_types = json.loads(raw_types) if raw_types else {}

        # Add owner addresses with "owner" type
        for netuid, address in owners.items():
            default_addresses[address] = [f'Owner-{netuid}', 'owner']

        try:
            # Close the substrate connection if it exists
            if hasattr(subtensor, 'substrate') and subtensor.substrate:
                # Close websocket connection
                if hasattr(subtensor.substrate, 'websocket') and subtensor.substrate.websocket:
                    subtensor.substrate.websocket.close()
                # Close the substrate itself
                subtensor.substrate.close()
        except Exception as e:
            pass  # Ignore cleanup errors
        finally:
            subtensor = None

        return {
            "addresses": default_addresses,
            "types": default_types
        }
    except Exception as e:
        logger.error(f"Error fetching subnet owner addresses: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

