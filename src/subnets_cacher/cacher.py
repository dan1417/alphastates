"""Cache full subnet info to Redis on every new block."""

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, Optional

import bittensor as bt
from bittensor.core.subtensor import Subtensor
from redis.asyncio import Redis
from bittensor.utils.balance import fixed_to_float

from src.common.config import settings

logger = logging.getLogger(__name__)

REDIS_KEY_SNAPSHOT = "subnets:snapshot"
REDIS_CHANNEL = "subnets:stream"
RECONNECT_BACKOFF_SECONDS = 5
ARCHIVE_RPC_URL = "wss://archive.minersunion.ai"


class SubnetsCacher:
    """Iterate TotalHotkeyAlpha + all_subnets on every block and cache the result in Redis."""

    def __init__(self, redis_client: Redis):
        self.redis = redis_client
        self.subtensor: Optional[Subtensor] = None
        self.archive_subtensor: Optional[Subtensor] = None
        self.running = False
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.executor = ThreadPoolExecutor(max_workers=1)

    async def start(self):
        """Start the cacher loop."""
        self.running = True
        self.loop = asyncio.get_event_loop()
        logger.info("🚀 Starting subnets cacher loop...")
        await self.loop.run_in_executor(self.executor, self._cacher_thread)

    async def stop(self):
        """Stop the cacher loop."""
        self.running = False
        self._cleanup_subtensor()
        self._cleanup_archive_subtensor()
        self.executor.shutdown(wait=False)
        logger.info("🛑 Stopping subnets cacher loop...")

    async def _read_prev_snapshot(self) -> dict:
        """Fetch and parse the previously-cached snapshot from Redis."""
        try:
            raw = await self.redis.get(REDIS_KEY_SNAPSHOT)
        except Exception as e:
            logger.warning(f"Could not read prior snapshot: {e}")
            return {}
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(f"Could not parse prior snapshot: {e}")
            return {}

    def _close_substrate(self, sub: Optional[Subtensor]):
        """Close a Subtensor's underlying substrate/websocket, swallowing errors."""
        if sub is None:
            return
        substrate = getattr(sub, "substrate", None)
        if substrate is not None:
            websocket = getattr(substrate, "websocket", None)
            if websocket is not None:
                try:
                    websocket.close()
                except Exception as e:
                    logger.debug(f"Error closing websocket: {e}")
            try:
                substrate.close()
            except Exception as e:
                logger.debug(f"Error closing substrate: {e}")

    def _cleanup_subtensor(self):
        """Tear down the subtensor instance and its underlying websocket/substrate."""
        if self.subtensor is None:
            return
        self._close_substrate(self.subtensor)
        self.subtensor = None
        logger.info("🧹 Cleaned up subtensor connection")

    def _cleanup_archive_subtensor(self):
        """Tear down the archive subtensor instance."""
        if self.archive_subtensor is None:
            return
        self._close_substrate(self.archive_subtensor)
        self.archive_subtensor = None
        logger.info("🧹 Cleaned up archive subtensor connection")

    def _connect_subtensor(self):
        """Establish a fresh subtensor connection."""
        t0 = time.perf_counter()
        self.subtensor = bt.Subtensor(network=settings.local_rpc_url)
        logger.info(
            f"✅ Connected to subtensor {settings.local_rpc_url} in "
            f"{time.perf_counter() - t0:.3f}s"
        )

    def _ensure_archive_subtensor(self):
        """Lazily connect to the archive node used for historical block timestamps."""
        if self.archive_subtensor is not None:
            return
        t0 = time.perf_counter()
        self.archive_subtensor = bt.Subtensor(network=ARCHIVE_RPC_URL)
        logger.info(
            f"✅ Connected to archive subtensor {ARCHIVE_RPC_URL} in "
            f"{time.perf_counter() - t0:.3f}s"
        )

    def _fetch_block_timestamp(self, block_number: int) -> Optional[int]:
        """Fetch the chain timestamp (unix seconds) for a historical block."""
        try:
            self._ensure_archive_subtensor()
            ts = self.archive_subtensor.get_timestamp(block_number)
        except Exception as e:
            logger.warning(
                f"❌ Failed to fetch timestamp for block {block_number}: {e}"
            )
            # Drop the archive connection so the next call retries fresh.
            self._cleanup_archive_subtensor()
            return None

        if isinstance(ts, datetime):
            return int(ts.timestamp())
        try:
            return int(ts)
        except (TypeError, ValueError):
            logger.warning(
                f"Unexpected timestamp type for block {block_number}: "
                f"{type(ts).__name__}"
            )
            return None

    def _cacher_thread(self):
        """Blocking loop that waits for new blocks and caches snapshots."""
        block: Optional[int] = None

        while self.running:
            try:
                if self.subtensor is None:
                    self._connect_subtensor()
                    block = self.subtensor.get_current_block()
                    logger.info(f"📊 Starting from block {block}")

                logger.info("----------------------------------------")
                logger.info(f"⏳ Waiting for block {block + 1}")
                self.subtensor.wait_for_block(block + 1)
                block += 1

                prev_snapshot = asyncio.run_coroutine_threadsafe(
                    self._read_prev_snapshot(),
                    self.loop,
                ).result()

                snapshot = self._build_snapshot(block, prev_snapshot)

                future = asyncio.run_coroutine_threadsafe(
                    self._write_snapshot(block, snapshot),
                    self.loop,
                )
                future.result()

            except Exception as e:
                logger.error(f"❌ Error in cacher thread: {e}", exc_info=True)
                self._cleanup_subtensor()
                time.sleep(RECONNECT_BACKOFF_SECONDS)

    def _build_snapshot(self, block: int, prev_snapshot: dict) -> dict:
        """Build the snapshot (per-subnet data + network-level fields) for the given block."""
        t0 = time.perf_counter()
        prev_subnets = prev_snapshot.get("subnets") or {}

        # get all hotkeys with stake for calculating liquidation price
        hotkeys = self.subtensor.substrate.query_map(
            module="SubtensorModule",
            storage_function="TotalHotkeyAlpha",
            params=[],
            page_size=1000,
        )

        # get all subnet info
        subnets = self.subtensor.all_subnets()
        network_immunity_period = self.subtensor.substrate.query(
            module="SubtensorModule",
            storage_function="NetworkImmunityPeriod",
            params=[],
            block_hash=None,
        )

        # get network registration lock cost
        lock_cost = self.subtensor.substrate.runtime_call(
            api='SubnetRegistrationRuntimeApi', method='get_network_registration_cost'
        )
        lock_cost = lock_cost.value / 10**9

        # get all ema taoflow for calculating total emissions and chain buy
        ema_taoflow_map = self.subtensor.substrate.query_map(
            module="SubtensorModule",
            storage_function="SubnetEmaTaoFlow",
            params=[],
            page_size=1000,
        )
        ema_taoflow_by_netuid = {
            int(getattr(k, "value", k)): max(fixed_to_float(getattr(v, "value", v)[1]), 0.0)
            for k, v in ema_taoflow_map
        }
        ema_taoflow_by_netuid[0] = 0
        total_ema_taoflow = sum([taoflow for taoflow in ema_taoflow_by_netuid.values() if taoflow > 0])
        total_emission_pct_by_netuid = {
            netuid: (value / total_ema_taoflow if value > 0 else 0.0)
            for netuid, value in ema_taoflow_by_netuid.items()
        }
        total_emission_pct_by_netuid[0] = 0
        chain_buy_by_netuid = {
            subnet.netuid: (0.5 * total_emission_pct_by_netuid[subnet.netuid] - subnet.tao_in_emission.tao) * 7200 for subnet in subnets
        }

        alpha_out_by_subnet: Dict[int, int] = {}
        for key, value in hotkeys:
            _hotkey_raw, netuid = key
            alpha = getattr(value, "value", value)
            alpha_out_by_subnet[netuid] = alpha_out_by_subnet.get(netuid, 0) + alpha
        logger.info(
            f"📊 Aggregated TotalHotkeyAlpha for block {block} in "
            f"{time.perf_counter() - t0:.3f}s"
        )

        final_subnets: Dict[int, dict] = {}
        for subnet in subnets:
            subnet_alpha_out = alpha_out_by_subnet.get(subnet.netuid, 0)
            liq_price = (
                subnet.tao_in.rao / subnet_alpha_out if subnet_alpha_out else 0
            )
            reg_block = subnet.network_registered_at
            # JSON object keys come back as strings after deserialization.
            prev = prev_subnets.get(str(subnet.netuid)) or prev_subnets.get(subnet.netuid)
            if (
                prev
                and prev.get("registered_at") == reg_block
                and prev.get("registered_at_timestamp") is not None
            ):
                registered_at_ts = prev["registered_at_timestamp"]
            else:
                registered_at_ts = self._fetch_block_timestamp(reg_block)
            final_subnets[subnet.netuid] = {
                'netuid': subnet.netuid,
                'subnet_identity': subnet.subnet_identity.__dict__ if subnet.subnet_identity else {},
                'tao_in': subnet.tao_in.tao,
                'alpha_in': subnet.alpha_in.tao,
                'alpha_out': subnet.alpha_out.tao,
                'price': subnet.price.tao,
                'liq_price': liq_price,
                'owner_coldkey': subnet.owner_coldkey,
                'owner_hotkey': subnet.owner_hotkey,
                'registered_at': reg_block,
                'registered_at_timestamp': registered_at_ts,
                'ema_price': subnet.moving_price,
                'is_immune': reg_block + network_immunity_period > block,
                'total_emission': total_emission_pct_by_netuid[subnet.netuid],
                'chain_buy': chain_buy_by_netuid[subnet.netuid]
            }

        return {'subnets': final_subnets, 'lock_cost': lock_cost}

    async def _write_snapshot(self, block: int, snapshot: dict):
        """Persist the snapshot to Redis and notify subscribers."""
        payload = {
            'block_number': block,
            'subnets': snapshot['subnets'],
            'lock_cost': snapshot['lock_cost'],
            'cached_at': int(datetime.utcnow().timestamp()),
        }
        try:
            await self.redis.set(REDIS_KEY_SNAPSHOT, json.dumps(payload))
            await self.redis.publish(
                REDIS_CHANNEL,
                json.dumps({'type': 'new_snapshot', 'block_number': block}),
            )
            logger.info(
                f"💾 Cached subnets snapshot for block {block} "
                f"({len(snapshot['subnets'])} subnets)"
            )
        except Exception as e:
            logger.error(f"❌ Failed to write snapshot to Redis: {e}", exc_info=True)
