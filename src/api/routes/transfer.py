"""API routes for transfer data."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import get_current_user
from src.common.db import get_session
from src.common.models import Transfer, User

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/range")
async def get_transfers_in_range(
    netuid: int,
    start_time: int,
    end_time: int,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Get top transfers for a specific netuid within a time range.
    Groups by (from_address, to_address) and sorts by total TAO amount.
    
    Args:
        netuid: The subnet ID (-1 for native TAO)
        start_time: Start timestamp (seconds)
        end_time: End timestamp (seconds)
        limit: Maximum number of transfer pairs to return (default: 50)
    
    Returns:
        Top transfer pairs sorted by total TAO amount (descending)
    """
    try:
        start_dt = datetime.fromtimestamp(start_time)
        end_dt = datetime.fromtimestamp(end_time)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp: {e}")
    
    logger.info(f"Getting top transfers for netuid={netuid}, start={start_dt}, end={end_dt}, limit={limit}")
    
    # Query to group transfers by (from_address, to_address) and sum TAO amounts
    query = select(
        Transfer.from_address,
        Transfer.to_address,
        func.count(Transfer.id).label('transfer_count'),
        func.sum(Transfer.tao_amount).label('total_tao_amount')
    ).where(
        Transfer.netuid == netuid,
        Transfer.is_coldkey_swap == False,
        Transfer.block_timestamp >= start_dt,
        Transfer.block_timestamp <= end_dt
    ).group_by(
        Transfer.from_address,
        Transfer.to_address
    ).order_by(
        func.sum(Transfer.tao_amount).desc()
    ).limit(limit)
    
    # Execute query
    result = await session.execute(query)
    transfer_groups = result.all()
    
    # Format response
    transfers = []
    for group in transfer_groups:
        transfer_data = {
            "from_address": group.from_address,
            "to_address": group.to_address,
            "transfer_count": group.transfer_count,
            "total_tao_amount": float(group.total_tao_amount) / 1e9,  # Convert to TAO (from rao)
        }
        transfers.append(transfer_data)
    
    return {
        "netuid": netuid,
        "start_time": start_time,
        "end_time": end_time,
        "transfers": transfers
    }


@router.get("/{address}")
async def get_transfers_by_address(
    address: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of transfer groups to return"),
    offset: int = Query(0, ge=0, description="Number of transfer groups to skip"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Get all transfer records grouped by (from_address, to_address) where the input address 
    is either from_address or to_address.
    
    Args:
        address: The wallet address to search for (SS58 format)
        limit: Maximum number of groups to return (1-1000, default: 100)
        offset: Number of groups to skip for pagination (default: 0)
    
    Returns:
        List of transfer groups with count and total amount, ordered by count (descending)
    """
    logger.info(f"Getting transfers for address={address}, limit={limit}, offset={offset}")
    
    # Validate address format (basic check for SS58 addresses)
    if not address or len(address) < 47 or len(address) > 48:
        raise HTTPException(
            status_code=400,
            detail="Invalid address format. Expected SS58 address (47-48 characters)"
        )
    
    # Build query to group transfers by (from_address, to_address)
    query = select(
        Transfer.from_address,
        Transfer.to_address,
        func.count(Transfer.id).label('transfer_count'),
        func.sum(Transfer.tao_amount).label('total_tao_amount')
    ).where(
        or_(
            Transfer.from_address == address,
            Transfer.to_address == address
        )
    ).group_by(
        Transfer.from_address,
        Transfer.to_address
    ).having(
        func.sum(Transfer.tao_amount) > 10000000
    ).order_by(
        func.count(Transfer.id).desc()
    )
    
    # Apply pagination
    query = query.limit(limit).offset(offset)
    
    # Execute query
    result = await session.execute(query)
    transfer_groups = result.all()
    
    # Format response
    transfer_list = []
    for group in transfer_groups:
        transfer_data = {
            "from_address": group.from_address,
            "to_address": group.to_address,
            "transfer_count": group.transfer_count,
            "total_tao_amount": float(group.total_tao_amount) / 1e9,  # Convert to TAO (from rao)
            # Indicate if this address was sender or receiver
            "direction": "sent" if group.from_address == address else "received"
        }
        transfer_list.append(transfer_data)
    
    # Get total count of unique groups for pagination metadata
    count_subquery = select(
        Transfer.from_address,
        Transfer.to_address
    ).where(
        or_(
            Transfer.from_address == address,
            Transfer.to_address == address
        )
    ).group_by(
        Transfer.from_address,
        Transfer.to_address
    ).subquery()
    
    count_query = select(func.count()).select_from(count_subquery)
    count_result = await session.execute(count_query)
    total_count = count_result.scalar() or 0
    
    return {
        "address": address,
        "transfers": transfer_list,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": total_count,
            "returned": len(transfer_list)
        }
    }


@router.get("/raw/{address}")
async def get_raw_transfers_for_address(
    address: str,
    limit: int = Query(50, ge=1, le=1000, description="Maximum number of transfers to return"),
    offset: int = Query(0, ge=0, description="Number of transfers to skip"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Get all raw transfer records for a specific address with pagination.
    
    Args:
        address: The wallet address to search for (SS58 format)
        limit: Maximum number of transfers to return (1-1000, default: 50)
        offset: Number of transfers to skip for pagination (default: 0)
    
    Returns:
        List of raw transfer records where address is either sender or receiver,
        sorted by timestamp (most recent first)
    """
    logger.info(f"Getting raw transfers for address={address}, limit={limit}, offset={offset}")
    
    # Validate address format
    if not address or len(address) < 47 or len(address) > 48:
        raise HTTPException(
            status_code=400,
            detail="Invalid address format. Expected SS58 address (47-48 characters)"
        )
    
    # Build query to get all transfers involving this address
    query = select(Transfer).where(
        or_(
            Transfer.from_address == address,
            Transfer.to_address == address
        )
    ).order_by(
        Transfer.id.desc()  # Most recent first
    )
    
    # Apply pagination
    query = query.limit(limit).offset(offset)
    
    # Execute query
    result = await session.execute(query)
    transfers = result.scalars().all()
    
    # Format response
    transfer_list = []
    for transfer in transfers:
        transfer_data = {
            "id": transfer.id,
            "from_address": transfer.from_address,
            "to_address": transfer.to_address,
            "netuid": transfer.netuid,
            "tao_amount": float(transfer.tao_amount) / 1e9,  # Convert to TAO (from rao)
            "timestamp": int(transfer.block_timestamp.timestamp()),
            "block_number": transfer.block_number,
            "extrinsic_index": transfer.extrinsic_index,
            "direction": "sent" if transfer.from_address == address else "received"
        }
        transfer_list.append(transfer_data)
    
    # Get total count for pagination metadata
    count_query = select(func.count(Transfer.id)).where(
        or_(
            Transfer.from_address == address,
            Transfer.to_address == address
        )
    )
    count_result = await session.execute(count_query)
    total_count = count_result.scalar() or 0
    
    return {
        "address": address,
        "transfers": transfer_list,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": total_count,
            "returned": len(transfer_list)
        }
    }


@router.get("/between/{from_address}/{to_address}")
async def get_transfers_between_addresses(
    from_address: str,
    to_address: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of transfers to return"),
    offset: int = Query(0, ge=0, description="Number of transfers to skip"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Get all raw transfer records between two addresses (bidirectional).
    
    Args:
        from_address: First wallet address (SS58 format)
        to_address: Second wallet address (SS58 format)
        limit: Maximum number of transfers to return (1-1000, default: 100)
        offset: Number of transfers to skip for pagination (default: 0)
    
    Returns:
        List of raw transfer records between the two addresses, sorted by timestamp (most recent first)
    """
    logger.info(f"Getting transfers between {from_address} and {to_address}, limit={limit}, offset={offset}")
    
    # Validate address formats
    if not from_address or len(from_address) < 47 or len(from_address) > 48:
        raise HTTPException(
            status_code=400,
            detail="Invalid from_address format. Expected SS58 address (47-48 characters)"
        )
    if not to_address or len(to_address) < 47 or len(to_address) > 48:
        raise HTTPException(
            status_code=400,
            detail="Invalid to_address format. Expected SS58 address (47-48 characters)"
        )
    
    # Build query to get all transfers between the two addresses (bidirectional)
    query = select(Transfer).where(
        or_(
            and_(
                Transfer.from_address == from_address,
                Transfer.to_address == to_address
            ),
            and_(
                Transfer.from_address == to_address,
                Transfer.to_address == from_address
            )
        )
    ).order_by(
        Transfer.id.desc()  # Most recent first
    )
    
    # Apply pagination
    query = query.limit(limit).offset(offset)
    
    # Execute query
    result = await session.execute(query)
    transfers = result.scalars().all()
    
    # Format response
    transfer_list = []
    for transfer in transfers:
        transfer_data = {
            "id": transfer.id,
            "from_address": transfer.from_address,
            "to_address": transfer.to_address,
            "netuid": transfer.netuid,
            "tao_amount": float(transfer.tao_amount) / 1e9,  # Convert to TAO (from rao)
            "timestamp": int(transfer.block_timestamp.timestamp()),
            "block_number": transfer.block_number,
            "extrinsic_index": transfer.extrinsic_index,
        }
        transfer_list.append(transfer_data)
    
    # Get total count for pagination metadata
    count_query = select(func.count(Transfer.id)).where(
        or_(
            and_(
                Transfer.from_address == from_address,
                Transfer.to_address == to_address
            ),
            and_(
                Transfer.from_address == to_address,
                Transfer.to_address == from_address
            )
        )
    )
    count_result = await session.execute(count_query)
    total_count = count_result.scalar() or 0
    
    return {
        "from_address": from_address,
        "to_address": to_address,
        "transfers": transfer_list,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": total_count,
            "returned": len(transfer_list)
        }
    }

