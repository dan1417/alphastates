"""API routes for trade data."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import get_current_user
from src.common.db import get_session
from src.common.models import Trade, User

router = APIRouter()


@router.get("/range")
async def get_trades_in_range(
    netuid: int,
    start_time: int,
    end_time: int,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Get top buyers and sellers for a specific netuid within a time range.
    
    Args:
        netuid: The subnet ID
        start_time: Start timestamp (seconds)
        end_time: End timestamp (seconds)
        limit: Maximum number of buyers and sellers to return (default: 20)
    
    Returns:
        Top buyers and sellers with their net alpha positions:
        - buyers: List of net buyers sorted by alpha volume (descending)
        - sellers: List of net sellers sorted by alpha volume (descending)
    """
    try:
        start_dt = datetime.fromtimestamp(start_time)
        end_dt = datetime.fromtimestamp(end_time)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp: {e}")
    
    # Query to calculate net position for each trader
    # Buy = +alpha, Sell = -alpha
    # CASE WHEN is_sell = false THEN alpha ELSE -alpha END
    query = select(
        Trade.address,
        func.sum(
            case(
                (Trade.is_sell == False, Trade.alpha),
                else_=-Trade.alpha
            )
        ).label('net_alpha'),
        func.sum(
            case(
                (Trade.is_sell == False, Trade.tao),
                else_=-Trade.tao
            )
        ).label('net_tao'),
        func.sum(
            case(
                (Trade.is_sell == False, Trade.tao),
                else_=0
            )
        ).label('total_buy_tao'),
        func.sum(
            case(
                (Trade.is_sell == True, Trade.tao),
                else_=0
            )
        ).label('total_sell_tao'),
        func.sum(
            case(
                (Trade.is_sell == False, Trade.alpha),
                else_=0
            )
        ).label('alpha_bought'),
        func.sum(
            case(
                (Trade.is_sell == True, Trade.alpha),
                else_=0
            )
        ).label('alpha_sold'),
        func.count(
            case(
                (Trade.is_sell == False, Trade.id),
                else_=None
            )
        ).label('buy_count'),
        func.count(
            case(
                (Trade.is_sell == True, Trade.id),
                else_=None
            )
        ).label('sell_count')
    ).where(
        Trade.netuid == netuid,
        Trade.is_coldkey_swapped == False,
        Trade.is_transferred == False,
        Trade.is_pure_move == False,
        Trade.block_timestamp >= start_dt,
        Trade.block_timestamp <= end_dt
    ).group_by(
        Trade.address
    ).order_by(
        func.sum(
            case(
                (Trade.is_sell == False, Trade.alpha),
                else_=-Trade.alpha
            )
        ).desc()
    )
    
    # Execute query
    result = await session.execute(query)
    traders = result.all()
    
    # Separate buyers (net_alpha > 0) and sellers (net_alpha < 0)
    buyers = []
    sellers = []
    gainers = []
    losers = []

    for trader in traders:
        net_alpha_value = float(trader.net_alpha) / 1e9
        net_tao_value = float(trader.net_tao) / 1e9
        alpha_bought_value = float(trader.alpha_bought or 0) / 1e9
        alpha_sold_value = float(trader.alpha_sold or 0) / 1e9
        tao_bought_value = float(trader.total_buy_tao or 0) / 1e9
        tao_sold_value = float(trader.total_sell_tao or 0) / 1e9
        
        trader_data = {
            "address": trader.address,
            "net_alpha": abs(net_alpha_value),  # Show absolute value
            "net_tao": abs(net_tao_value),  # Show absolute value
            "buy_tao": tao_bought_value,
            "sell_tao": tao_sold_value,
            "buy_count": trader.buy_count or 0,
            "sell_count": trader.sell_count or 0
        }

        if trader.address == '5EyvRbmfhR9JQvjzu1pPis1JsPaazQbEmhVqCrp2eAUn6eFk':
            logging.info(trader_data)
            logging.info(f"net_alpha_value: {net_alpha_value}")
            logging.info(f"net_tao_value: {net_tao_value}")
            logging.info(f"alpha_bought_value: {alpha_bought_value}")
            logging.info(f"alpha_sold_value: {alpha_sold_value}")
            logging.info(f"tao_bought_value: {tao_bought_value}")
            logging.info(f"tao_sold_value: {tao_sold_value}")
        
        if net_alpha_value > 1:
            buyers.append(trader_data)
        elif net_alpha_value < -1:
            sellers.append(trader_data)
        
        # Calculate gainers/losers
        # Conditions: alpha_sold > alpha_bought AND (alpha_sold - alpha_bought) / alpha_bought < 0.01
        if alpha_bought_value > 0:  # Avoid division by zero
            alpha_ratio = (alpha_sold_value - alpha_bought_value) / alpha_bought_value
            if alpha_sold_value > alpha_bought_value * 0.9995 - 1 and alpha_ratio < 0.25: # for very quick buy/sell, we need to apply this custom logic
                tao_net = tao_sold_value - tao_bought_value
                gainer_loser_data = {
                    "address": trader.address,
                    "tao_net": tao_net,
                    "tao_bought": tao_bought_value,
                    "tao_sold": tao_sold_value,
                    "alpha_bought": alpha_bought_value,
                    "alpha_sold": alpha_sold_value,
                    "buy_count": trader.buy_count or 0,
                    "sell_count": trader.sell_count or 0
                }
                
                if tao_net > 0:
                    gainers.append(gainer_loser_data)
                elif tao_net < 0:
                    losers.append(gainer_loser_data)
    
    # Sellers are already sorted by net_alpha desc (most negative first)
    # We want to show them sorted by absolute value, so reverse
    sellers.reverse()
    
    # Sort gainers by tao_net descending (highest gain first)
    gainers.sort(key=lambda x: x["tao_net"], reverse=True)
    
    # Sort losers by tao_net ascending (most negative first, i.e., biggest loss first)
    losers.sort(key=lambda x: x["tao_net"])
    
    # Limit results
    buyers = buyers[:limit]
    sellers = sellers[:limit]
    gainers = gainers[:limit]
    losers = losers[:limit]
    
    return {
        "netuid": netuid,
        "start_time": start_time,
        "end_time": end_time,
        "buyers": buyers,
        "sellers": sellers,
        "gainers": gainers,
        "losers": losers
    }


@router.get("/address")
async def get_address_trades(
    netuid: int,
    address: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Get all trades for a specific address in a subnet.
    
    Args:
        netuid: The subnet ID
        address: The trader's address
    
    Returns:
        List of all trades for the address, sorted by timestamp:
        - trades: List of trade objects with timestamp, price, amount, type
    """
    # Query all trades for this address in this subnet
    query = select(Trade).where(
        Trade.netuid == netuid,
        Trade.address == address,
        Trade.is_coldkey_swapped == False,
        Trade.is_transferred == False,
        Trade.is_pure_move == False,
    ).order_by(Trade.block_timestamp.asc())
    
    # Execute query
    result = await session.execute(query)
    trades = result.scalars().all()
    
    # Format trades for frontend
    trade_list = []
    for trade in trades:
        trade_list.append({
            "timestamp": int(trade.block_timestamp.timestamp()),
            "is_sell": trade.is_sell,
            "tao": float(trade.tao) / 1e9,
            "alpha": float(trade.alpha) / 1e9,
            "price": float(trade.price) / 1e9,
            "block_number": trade.block_number
        })
    
    return {
        "netuid": netuid,
        "address": address,
        "trade_count": len(trade_list),
        "trades": trade_list
    }


@router.get("/all/{address}")
async def get_all_trades_for_address(
    address: str,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Get all trades for a specific address across all subnets with pagination.
    
    Args:
        address: The trader's address
        limit: Maximum number of trades to return (default: 50)
        offset: Number of trades to skip for pagination (default: 0)
    
    Returns:
        Paginated list of all trades for the address, sorted by timestamp (most recent first)
    """
    # Query all trades for this address across all subnets
    query = select(Trade).where(
        Trade.address == address,
        Trade.is_coldkey_swapped == False,
        Trade.is_transferred == False,
        Trade.is_pure_move == False,
    ).order_by(Trade.id.desc())
    
    # Get total count
    count_query = select(func.count(Trade.id)).where(
        Trade.address == address,
        Trade.is_coldkey_swapped == False,
        Trade.is_transferred == False,
        Trade.is_pure_move == False,
    )
    count_result = await session.execute(count_query)
    total_count = count_result.scalar() or 0
    
    # Apply pagination
    query = query.limit(limit).offset(offset)
    
    # Execute query
    result = await session.execute(query)
    trades = result.scalars().all()
    
    # Format trades for frontend
    trade_list = []
    for trade in trades:
        trade_list.append({
            "timestamp": int(trade.block_timestamp.timestamp()),
            "netuid": trade.netuid,
            "is_sell": trade.is_sell,
            "tao": float(trade.tao) / 1e9,
            "alpha": float(trade.alpha) / 1e9,
            "price": float(trade.price) / 1e9,
            "block_number": trade.block_number,
            "extrinsic_index": trade.extrinsic_index
        })
    
    return {
        "address": address,
        "trades": trade_list,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": total_count,
            "returned": len(trade_list)
        }
    }

