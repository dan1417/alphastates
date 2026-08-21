"""Sync polling of pending extrinsics; returns current pending set and new interested calls."""

import logging
import time
from collections import deque
from typing import Callable, List, Optional, Set, Tuple

import redis
from substrateinterface import SubstrateInterface

from .proxy_cache import get_proxy_delegates
from .serializer import _ensure_dict, build_pending_message, extract_interested_calls

logger = logging.getLogger(__name__)

MAX_SEEN = 1_000


def _ext_id(ext) -> str:
    """Get stable ext_id from an extrinsic."""
    return (
        ext.data.to_hex()
        if hasattr(ext, "data") and hasattr(ext.data, "to_hex")
        else str(ext)
    )


def poll_once(
    substrate: SubstrateInterface,
    seen_set: set,
    seen_order: deque,
) -> Tuple[Set[str], List[Tuple[str, str, dict, str, str]]]:
    """
    Fetch pending extrinsics once. Returns (current_pending_ids, new_interested_calls).
    current_pending_ids = set of all ext_id in the current mempool.
    new_interested_calls = list of (ext_id, address, call, display_type, proxy_address)
    for new interested calls only.
    """
    try:
        extrinsics = substrate.retrieve_pending_extrinsics()
    except Exception as e:
        logger.warning("RPC error: %s", e)
        return (set(), [])

    current_pending_ids: Set[str] = set()
    results: List[Tuple[str, str, dict, str, str]] = []

    for ext in extrinsics:
        ext_id = _ext_id(ext)
        current_pending_ids.add(ext_id)

        if ext_id in seen_set:
            continue
        if len(seen_order) >= MAX_SEEN:
            seen_set.discard(seen_order[0])
        seen_set.add(ext_id)
        seen_order.append(ext_id)

        try:
            decoded = _ensure_dict(ext.value if hasattr(ext, "value") else ext)
            address = decoded.get("address")
            call = decoded.get("call")
            if call is None:
                continue
            call = _ensure_dict(call)
            address_str = str(address) if address is not None else ""
            for addr, c, display_type, proxy_address in extract_interested_calls(address_str, call):
                results.append((ext_id, addr, c, display_type, proxy_address))
        except Exception as e:
            logger.debug("Decode error: %s", e)

    return (current_pending_ids, results)


def run_poll_loop(
    url: str,
    on_message: Callable,
    on_pending_ids: Callable[[Set[str]], None],
    poll_interval: float = 1.0,
    redis_client: Optional[redis.Redis] = None,
):
    """
    Run forever: connect, poll pending extrinsics.
    on_message(msg_dict) for each new interested call.
    on_pending_ids(ids) with the full set of ext_ids currently in the mempool (each poll).
    redis_client: when provided, proxy_valid is resolved from the proxy cache for each
        proxy call before building the message.
    """
    substrate = SubstrateInterface(url=url)
    seen_set: set = set()
    seen_order: deque = deque(maxlen=MAX_SEEN)

    while True:
        current_pending_ids, interested = poll_once(substrate, seen_set, seen_order)
        for ext_id, address, call, display_type, proxy_address in interested:
            try:
                proxy_valid: bool | None = None
                if proxy_address and redis_client is not None:
                    delegates = get_proxy_delegates(redis_client, address)
                    proxy_valid = proxy_address in delegates
                msg = build_pending_message(
                    ext_id, address, call, display_type, proxy_address, proxy_valid
                )
                on_message(msg)
            except Exception as e:
                logger.warning("Build/on_message error: %s", e)

        try:
            on_pending_ids(current_pending_ids)
        except Exception as e:
            logger.warning("on_pending_ids error: %s", e)

        time.sleep(poll_interval)
