"""
Mempool watcher service: polls pending extrinsics and publishes
add_stake / remove_stake / transfer calls to Redis. On each new block,
publishes subnet prices so frontend can convert alpha amounts to TAO.
"""

import json
import logging
import os
import threading

import redis

from .poller import run_poll_loop
from .proxy_cache import run_proxy_cache_loop
from .subnet_prices import run_subnet_prices_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

RPC_URL = os.environ.get("RPC_URL", "http://localhost:9944")
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
MEMPOOL_REDIS_CHANNEL = os.environ.get("MEMPOOL_REDIS_CHANNEL", "mempool:stream")
POLL_INTERVAL = float(os.environ.get("MEMPOOL_POLL_INTERVAL", "1.0"))
BLOCK_POLL_INTERVAL = float(os.environ.get("MEMPOOL_BLOCK_POLL_INTERVAL", "6.0"))


def main():
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

    def publish(msg):
        try:
            redis_client.publish(MEMPOOL_REDIS_CHANNEL, json.dumps(msg))
        except Exception as e:
            logger.warning("Redis publish error: %s", e)

    def on_pending_ids(ids_set):
        publish({"type": "pending_extrinsic_ids", "ids": list(ids_set)})

    logger.info("Mempool watcher started (RPC=%s, Redis=%s:%s, channel=%s)", RPC_URL, REDIS_HOST, REDIS_PORT, MEMPOOL_REDIS_CHANNEL)

    # Thread: subnet prices on each new block (for alpha -> TAO conversion on frontend)
    prices_thread = threading.Thread(
        target=run_subnet_prices_loop,
        args=(RPC_URL, publish),
        kwargs={"poll_interval": BLOCK_POLL_INTERVAL},
        daemon=True,
    )
    prices_thread.start()
    logger.info("Subnet prices thread started (poll every %s s)", BLOCK_POLL_INTERVAL)

    # Thread: proxy relationships cache – refreshed every block, stored in Redis
    proxy_thread = threading.Thread(
        target=run_proxy_cache_loop,
        args=(RPC_URL, redis_client),
        kwargs={"poll_interval": BLOCK_POLL_INTERVAL},
        daemon=True,
    )
    proxy_thread.start()
    logger.info("Proxy cache thread started (poll every %s s)", BLOCK_POLL_INTERVAL)

    # Main: pending extrinsics poll loop
    run_poll_loop(
        RPC_URL,
        on_message=publish,
        on_pending_ids=on_pending_ids,
        poll_interval=POLL_INTERVAL,
        redis_client=redis_client,
    )


if __name__ == "__main__":
    main()
