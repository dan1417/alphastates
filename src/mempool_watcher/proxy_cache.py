"""
Proxy cache loop: every block, fetch all proxy relationships via
subtensor.get_proxies() and store them in Redis so the mempool watcher
can validate proxy calls without blocking on-chain lookups.

Redis structure:
  Key  : mempool:proxies  (Hash)
  Field: real_addr (SS58)
  Value: JSON list of [delegate_ss58, proxy_type] pairs
  TTL  : PROXY_CACHE_TTL seconds – auto-expires if this loop dies
"""

import json
import logging
import time

import redis

logger = logging.getLogger(__name__)

PROXY_CACHE_KEY = "mempool:proxies"
PROXY_CACHE_TTL = 60  # seconds


def _parse_entries(raw) -> list[tuple[str, str]]:
    """Parse a cache value into [(delegate, proxy_type), ...].

    Accepts both the new [[delegate, proxy_type], ...] format and the
    legacy [delegate, ...] format so the cache can be read during a
    rollout where the writer hasn't been redeployed yet.
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


def get_proxy_delegates(redis_client: redis.Redis, real_addr: str) -> set[str]:
    """Return the set of delegate addresses for real_addr from the cache."""
    raw = redis_client.hget(PROXY_CACHE_KEY, real_addr)
    return {delegate for delegate, _ in _parse_entries(raw)}


def get_proxies_for_real(
    redis_client: redis.Redis, real_addr: str
) -> list[dict]:
    """Return [{delegate, proxy_type}, ...] for proxies added by real_addr."""
    raw = redis_client.hget(PROXY_CACHE_KEY, real_addr)
    return [
        {"delegate": delegate, "proxy_type": proxy_type}
        for delegate, proxy_type in _parse_entries(raw)
    ]


def get_reals_for_delegate(
    redis_client: redis.Redis, delegate_addr: str
) -> list[dict]:
    """Return [{real_addr, proxy_type}, ...] for accounts that added delegate_addr as a proxy."""
    all_entries = redis_client.hgetall(PROXY_CACHE_KEY) or {}
    out: list[dict] = []
    for real_addr, raw in all_entries.items():
        for delegate, proxy_type in _parse_entries(raw):
            if delegate == delegate_addr:
                out.append({"real_addr": real_addr, "proxy_type": proxy_type})
    return out


def run_proxy_cache_loop(
    rpc_url: str,
    redis_client: redis.Redis,
    poll_interval: float = 6.0,
):
    """
    Run forever: on each new block, fetch all proxy relationships and cache
    them in Redis so mempool_watcher can add proxy_valid to pending messages.
    """
    try:
        import bittensor as bt
    except ImportError:
        logger.warning("bittensor not installed; proxy cache will not run")
        return

    last_block = 0
    subtensor = None

    while True:
        try:
            if subtensor is None:
                subtensor = bt.Subtensor(network=rpc_url)
                logger.info("Proxy cache: connected to %s", rpc_url)

            current_block = subtensor.get_current_block()
            if current_block != last_block and current_block > 0:
                try:
                    proxies = subtensor.get_proxies(block=current_block)
                    mapping = {
                        real_addr: json.dumps(
                            [[p.delegate, p.proxy_type] for p in proxy_list]
                        )
                        for real_addr, proxy_list in proxies.items()
                    }
                    pipe = redis_client.pipeline()
                    pipe.delete(PROXY_CACHE_KEY)
                    if mapping:
                        pipe.hset(PROXY_CACHE_KEY, mapping=mapping)
                    pipe.expire(PROXY_CACHE_KEY, PROXY_CACHE_TTL)
                    pipe.execute()
                    logger.debug(
                        "Proxy cache updated for block %s (%s real accounts)",
                        current_block,
                        len(mapping),
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to update proxy cache for block %s: %s", current_block, e
                    )
                last_block = current_block
        except Exception as e:
            logger.warning("Proxy cache loop error: %s", e)
            subtensor = None

        time.sleep(poll_interval)
