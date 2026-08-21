"""Standalone Subnets Cacher Service."""

import asyncio
import logging

from src.common.cache import init_cache
from src.subnets_cacher.cacher import SubnetsCacher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    """Run the subnets cacher service."""
    logger.info("🚀 Starting Subnets Cacher Service...")

    redis_client = await init_cache()
    logger.info("✅ Connected to Redis")

    cacher = SubnetsCacher(redis_client)

    try:
        await cacher.start()
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down Subnets Cacher Service...")
        await cacher.stop()


if __name__ == "__main__":
    asyncio.run(main())
