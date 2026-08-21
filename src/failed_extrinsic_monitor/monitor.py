"""Monitor for failed extrinsics on each new block."""

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict

import bittensor as bt
from bittensor.core.subtensor import Subtensor
import redis.asyncio as aioredis

from src.common.config import settings
from src.common.db import async_session_factory
from src.common.models import FailedTrade
from src.mempool_watcher.serializer import (
    DISPLAY_ADD_STAKE,
    DISPLAY_MOVE_STAKE,
    DISPLAY_REMOVE_STAKE,
    DISPLAY_SWAP_STAKE,
    _ensure_dict,
    _to_serializable,
    extract_interested_calls,
    get_call_args_dict,
)

logger = logging.getLogger(__name__)

TRADE_DISPLAY_TYPES = {
    DISPLAY_ADD_STAKE,
    DISPLAY_REMOVE_STAKE,
    DISPLAY_MOVE_STAKE,
    DISPLAY_SWAP_STAKE,
}


class FailedExtrinsicMonitor:
    """Listen for new blocks and save failed trade extrinsics to DB."""

    def __init__(self):
        self.subtensor: Subtensor | None = None
        self.substrate = None
        self.running = False
        self.loop = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self._redis: aioredis.Redis | None = None
        # pallet_index -> error_index -> {name, pallet, docs}
        self._error_registry: Dict[int, Dict[int, dict]] = {}

    async def start(self):
        """Start monitoring for failed extrinsics."""
        self.running = True
        self.loop = asyncio.get_event_loop()
        self._redis = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            decode_responses=True,
        )
        logger.info("🚀 Starting Failed Extrinsic Monitor...")
        await self.loop.run_in_executor(self.executor, self._monitor_thread)

    async def stop(self):
        """Stop monitoring."""
        self.running = False
        self.executor.shutdown(wait=False)
        if self._redis:
            await self._redis.aclose()
        logger.info("🛑 Stopping Failed Extrinsic Monitor...")

    # ── Error registry ─────────────────────────────────────────────────────

    def _build_error_registry(self) -> Dict[int, Dict[int, dict]]:
        """
        Walk the runtime metadata (V14) and build:
            pallet_index -> error_index -> {pallet, name, docs}

        This is called once after connecting so we can decode bare
        `{'Module': {'index': N, 'error': '0x...'}}` dispatch errors into
        human-readable strings like "SubtensorModule.StakeNotEnoughToWithdraw".
        """
        registry: Dict[int, Dict[int, dict]] = {}
        try:
            meta = self.substrate.metadata
            pallets = meta.value[1]["V14"]["pallets"]

            types_by_id = {t["id"]: t["type"] for t in meta.value[1]["V14"]["types"]["types"]}

            for pallet in pallets:
                pallet_idx: int = pallet["index"]
                pallet_name: str = pallet["name"]

                error_def = pallet.get("error")

                if not error_def:
                    continue
                error_type_id = error_def["ty"]
                type_def = types_by_id.get(error_type_id, {})

                variants = (
                    type_def.get("def", {}).get("variant", {}).get("variants", [])
                )

                registry[pallet_idx] = {
                    v["index"]: {
                        "pallet": pallet_name,
                        "name": v["name"],
                        "docs": " ".join(v.get("docs", [])).strip(),
                    }
                    for v in variants
                }
        except Exception as e:
            logger.warning(f"Could not build error registry from metadata: {e}")
        return registry

    def _decode_dispatch_error(self, dispatch_error) -> str:
        """
        Decode a raw dispatch_error value into a human-readable string.

        Module errors are resolved via the registry built from metadata.
        Examples:
            {'Module': {'index': 7, 'error': '0x09000000'}}
                → "SubtensorModule.StakeNotEnoughToWithdraw: ..."
            {'BadOrigin': None}  → "BadOrigin"
        """
        if not isinstance(dispatch_error, dict):
            return str(dispatch_error)[:255]

        if "Module" in dispatch_error:
            module = dispatch_error["Module"]
            pallet_idx = module.get("index")
            error_hex = module.get("error", "0x00000000")
            try:
                # First byte of the little-endian 4-byte value is the error index.
                error_idx = int(error_hex[2:4], 16)
            except Exception:
                error_idx = 0

            info = self._error_registry.get(pallet_idx, {}).get(error_idx)
            if info:
                msg = f"{info['pallet']}.{info['name']}"
                if info["docs"]:
                    msg += f": {info['docs']}"
                return msg[:255]
            # Fallback: pallet unknown or error not in registry
            return f"Module(pallet={pallet_idx}, error_index={error_idx})"

        # Other Substrate error types
        for known in ("BadOrigin", "CannotLookup", "ConsumerRemaining",
                      "NoProviders", "TooManyConsumers", "Exhausted",
                      "Corruption", "Unavailable", "RootNotAllowed"):
            if known in dispatch_error:
                return known

        if "Other" in dispatch_error:
            return f"Other: {dispatch_error['Other']}"[:255]

        # Generic fallback
        return str(_to_serializable(dispatch_error))[:255]

    # ── Monitor thread ─────────────────────────────────────────────────────

    def _monitor_thread(self):
        """Blocking monitor thread: wait for blocks, schedule async processing."""
        try:
            self.subtensor = bt.Subtensor(network=settings.rpc_url)
            self.substrate = self.subtensor.substrate
            logger.info(f"✅ Connected to Bittensor network: {settings.rpc_url}")
        except Exception as e:
            logger.error(f"❌ Failed to connect to Bittensor: {e}")
            return

        # Build error registry from the current runtime metadata
        self._error_registry = self._build_error_registry()

        current_block = self.subtensor.get_current_block()
        logger.info(f"📊 Starting from block: {current_block}")

        while self.running:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._process_block(current_block),
                    self.loop,
                )
                future.result()

                current_block += 1
                logger.info(f"🔍 Waiting for block: {current_block}")
                self.subtensor.wait_for_block(current_block)

            except Exception as e:
                logger.error(f"❌ Error in monitor thread: {e}", exc_info=True)
                time.sleep(12)

    async def _process_block(self, block_number: int):
        """Fetch failed trade extrinsics for the block, persist to DB, and publish."""
        try:
            logger.info(f"🔍 Processing block {block_number}...")

            block_header = self.substrate.get_block_header(block_number=block_number)
            block_hash = block_header["header"]["hash"]

            block_data = self.substrate.get_block(block_hash=block_hash)
            extrinsics = block_data["extrinsics"]

            events = self.substrate.get_events(block_hash=block_hash)
            timestamp = self.subtensor.get_timestamp(block_number).timestamp()
            block_timestamp = datetime.fromtimestamp(timestamp, tz=timezone.utc)

            # Build extrinsic_idx -> decoded error message from ExtrinsicFailed events
            failed_error_map: Dict[int, str] = {}
            for event in events:
                event_id = event["event"]["event_id"]
                idx = event["extrinsic_idx"]
                if idx is None:
                    continue

                attrs = event["event"]["attributes"]

                if event_id == "ExtrinsicFailed":
                    if isinstance(attrs, dict):
                        dispatch_error = attrs.get("dispatch_error", attrs)
                    elif isinstance(attrs, (list, tuple)):
                        dispatch_error = attrs[0] if attrs else "unknown"
                    else:
                        dispatch_error = attrs
                    failed_error_map[idx] = self._decode_dispatch_error(
                        _to_serializable(dispatch_error)
                    )

                elif event_id == "ProxyExecuted":
                    # ProxyExecuted attrs look like {'result': {'Err': {...}}}
                    # Only capture when there is an error.
                    result = attrs.get("result") if isinstance(attrs, dict) else None
                    if isinstance(result, dict) and "Err" in result:
                        # Don't overwrite a genuine ExtrinsicFailed error if present
                        if idx not in failed_error_map:
                            failed_error_map[idx] = self._decode_dispatch_error(
                                _to_serializable(result["Err"])
                            )

            if not failed_error_map:
                logger.debug(f"No failed extrinsics in block {block_number}")
                return

            logger.info(
                f"⚠️  Block {block_number}: {len(failed_error_map)} failed extrinsic(s) "
                f"at indices {sorted(failed_error_map)}"
            )

            records = []
            publish_messages = []

            for idx, error_message in sorted(failed_error_map.items()):
                if idx >= len(extrinsics):
                    logger.warning(
                        f"  Index {idx} out of range (block has {len(extrinsics)} extrinsics)"
                    )
                    continue

                ext = extrinsics[idx]
                try:
                    decoded = _ensure_dict(ext.value if hasattr(ext, "value") else ext)
                    address = decoded.get("address")
                    call = decoded.get("call")
                    if call is None:
                        continue

                    call = _ensure_dict(call)
                    address_str = str(address) if address is not None else ""
                    interested = extract_interested_calls(address_str, call)

                    for addr, c, display_type, proxy_address in interested:
                        if display_type not in TRADE_DISPLAY_TYPES:
                            continue

                        call_args = get_call_args_dict(c)

                        # Skip same-netuid move/swap (not a real cross-subnet trade)
                        if display_type in (DISPLAY_MOVE_STAKE, DISPLAY_SWAP_STAKE):
                            if call_args.get("origin_netuid") == call_args.get("destination_netuid"):
                                continue
                        record = FailedTrade(
                            block_number=block_number,
                            block_hash=block_hash,
                            block_timestamp=block_timestamp,
                            extrinsic_index=idx,
                            error_message=error_message,
                            address=addr,
                            proxy_address=proxy_address or "",
                            call_module=c.get("call_module", ""),
                            call_function=c.get("call_function", ""),
                            call_args=call_args,
                        )
                        records.append(record)

                        publish_messages.append({
                            "type": "failed_trade",
                            "block_number": block_number,
                            "block_hash": block_hash,
                            "block_timestamp": int(block_timestamp.timestamp()),
                            "extrinsic_index": idx,
                            "error_message": error_message,
                            "address": addr,
                            "proxy_address": proxy_address or "",
                            "call_module": c.get("call_module", ""),
                            "call_function": c.get("call_function", ""),
                            "call_args": call_args,
                        })

                        logger.info(
                            f"  [{block_number}:{idx}] FAILED "
                            f"address={addr} "
                            f"module={c.get('call_module')} "
                            f"function={c.get('call_function')} "
                            f"error={error_message}"
                            + (f" proxy={proxy_address}" if proxy_address else "")
                        )

                except Exception as e:
                    logger.warning(
                        f"  [{block_number}:{idx}] Could not decode extrinsic: {e}"
                    )

            if records:
                async with async_session_factory() as session:
                    session.add_all(records)
                    await session.commit()
                logger.info(
                    f"💾 Saved {len(records)} failed trade(s) for block {block_number}"
                )

                if self._redis:
                    for msg in publish_messages:
                        try:
                            await self._redis.publish(
                                "failed_trades:stream", json.dumps(msg)
                            )
                        except Exception as e:
                            logger.warning(f"Failed to publish to Redis: {e}")

        except Exception as e:
            logger.error(f"❌ Error processing block {block_number}: {e}", exc_info=True)
