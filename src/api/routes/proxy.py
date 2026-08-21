"""API routes for proxy data (from mempool proxy cache in Redis)."""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException

from src.api.auth import get_current_user
from src.common.cache import get_cache
from src.common.models import User
from src.common.subtensor_pool import get_subtensor_pool
from src.mempool_watcher.proxy_cache import PROXY_CACHE_KEY

logger = logging.getLogger(__name__)

router = APIRouter()

# get_stake_info_for_coldkeys accepts at most this many SS58s per call before
# the runtime API rejects the request as a vec-length violation.
STAKE_BATCH_SIZE = 63


def _parse_entries(raw) -> list[tuple[str, str]]:
    """Parse a cache value into [(delegate, proxy_type), ...].

    Accepts both the new [[delegate, proxy_type], ...] format and the
    legacy [delegate, ...] format.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    for item in data:
        if isinstance(item, str):
            out.append((item, ""))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append((str(item[0]), str(item[1])))
    return out


@router.get("/{address}")
async def get_proxy_info(
    address: str,
    current_user: User = Depends(get_current_user),
):
    """
    Return proxy relationships involving `address`.

    Source: the `mempool:proxies` Redis hash maintained by
    src.mempool_watcher.proxy_cache. The cache is refreshed every block
    with a 60s TTL, so an empty response means the cache hasn't been
    populated (mempool_watcher may not be running).

    Response:
        {
            "address": <ss58>,
            "outgoing": [{"delegate": <ss58>, "proxy_type": <str>}, ...],
            "incoming": [{"real_addr": <ss58>, "proxy_type": <str>}, ...]
        }
    """
    if not address or len(address) < 47 or len(address) > 48:
        raise HTTPException(
            status_code=400,
            detail="Invalid address format. Expected SS58 address (47-48 characters).",
        )

    cache = await get_cache()
    all_entries = await cache.hgetall(PROXY_CACHE_KEY) or {}

    outgoing: list[dict] = []
    incoming: list[dict] = []

    for real_addr, raw in all_entries.items():
        entries = _parse_entries(raw)
        if real_addr == address:
            for delegate, proxy_type in entries:
                outgoing.append({"delegate": delegate, "proxy_type": proxy_type})
        for delegate, proxy_type in entries:
            if delegate == address:
                incoming.append({"real_addr": real_addr, "proxy_type": proxy_type})

    return {
        "address": address,
        "outgoing": outgoing,
        "incoming": incoming,
    }


async def _fetch_incoming_delegators(address: str) -> list[str]:
    """Return all real_addrs that added `address` as a proxy."""
    cache = await get_cache()
    all_entries = await cache.hgetall(PROXY_CACHE_KEY) or {}
    delegators: list[str] = []
    for real_addr, raw in all_entries.items():
        for delegate, _proxy_type in _parse_entries(raw):
            if delegate == address:
                delegators.append(real_addr)
                break
    return delegators


def _fetch_stake_batch(subtensor, batch: list[str]) -> dict:
    """Blocking helper run in a thread: fetch stake info for a batch of coldkeys."""
    return subtensor.get_stake_info_for_coldkeys(batch)


async def _fetch_stakes_for_batch(batch: list[str]) -> dict:
    """Acquire a pooled subtensor and fetch stake info for one batch."""
    pool = get_subtensor_pool()
    loop = asyncio.get_event_loop()
    async with pool.get_connection() as subtensor:
        return await loop.run_in_executor(None, _fetch_stake_batch, subtensor, batch)


@router.get("/{address}/stake-summary")
async def get_proxy_stake_summary(
    address: str,
    current_user: User = Depends(get_current_user),
):
    """
    Aggregate stake info for all delegators that added `address` as a proxy.

    For each delegator the response includes per-subnet stake (alpha and tao)
    and the delegator's total tao value. A `totals_by_subnet` map and
    `grand_total_tao` aggregate across all delegators.

    The frontend computes percentages from these raw numbers:
        - per-delegator subnet %  = stake_tao / delegator.total_tao
        - aggregate subnet %      = totals_by_subnet[netuid].tao / grand_total_tao
    """
    if not address or len(address) < 47 or len(address) > 48:
        raise HTTPException(
            status_code=400,
            detail="Invalid address format. Expected SS58 address (47-48 characters).",
        )

    delegators = await _fetch_incoming_delegators(address)

    if not delegators:
        return {
            "address": address,
            "delegators": [],
            "totals_by_subnet": {},
            "grand_total_tao": 0.0,
        }

    # Fetch subnet prices once, then fan out stake-info batches across the pool.
    pool = get_subtensor_pool()
    loop = asyncio.get_event_loop()
    async with pool.get_connection() as subtensor:
        subnet_prices = await loop.run_in_executor(None, subtensor.get_subnet_prices)

    batches = [
        delegators[i : i + STAKE_BATCH_SIZE]
        for i in range(0, len(delegators), STAKE_BATCH_SIZE)
    ]
    batch_results = await asyncio.gather(*[_fetch_stakes_for_batch(b) for b in batches])

    stakes_by_address: dict[str, list] = {}
    for partial in batch_results:
        stakes_by_address.update(partial)

    delegator_rows: list[dict] = []
    totals_by_subnet: dict[int, dict[str, float]] = {}
    grand_total_tao = 0.0

    for delegator_addr in delegators:
        stakes = stakes_by_address.get(delegator_addr, [])
        stakes_by_subnet: dict[int, dict[str, float]] = {}
        for stake in stakes:
            netuid = stake.netuid
            alpha = float(stake.stake.tao)  # Balance.tao yields the human-readable amount
            price_tao = subnet_prices[netuid].tao if netuid in subnet_prices else 0.0
            tao_value = alpha * price_tao

            entry = stakes_by_subnet.setdefault(netuid, {"alpha": 0.0, "tao": 0.0})
            entry["alpha"] += alpha
            entry["tao"] += tao_value

        delegator_total_tao = sum(v["tao"] for v in stakes_by_subnet.values())
        grand_total_tao += delegator_total_tao

        for netuid, v in stakes_by_subnet.items():
            tot = totals_by_subnet.setdefault(netuid, {"alpha": 0.0, "tao": 0.0})
            tot["alpha"] += v["alpha"]
            tot["tao"] += v["tao"]

        delegator_rows.append(
            {
                "address": delegator_addr,
                "stakes_by_subnet": {
                    str(netuid): v for netuid, v in stakes_by_subnet.items()
                },
                "total_tao": delegator_total_tao,
            }
        )

    return {
        "address": address,
        "delegators": delegator_rows,
        "totals_by_subnet": {str(netuid): v for netuid, v in totals_by_subnet.items()},
        "grand_total_tao": grand_total_tao,
    }
