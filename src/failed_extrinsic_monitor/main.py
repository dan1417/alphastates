"""Standalone Failed Extrinsic Monitor Service."""

import asyncio
import logging

from src.common.db import init_db
from src.failed_extrinsic_monitor.monitor import FailedExtrinsicMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    """Run the failed extrinsic monitor service."""
    logger.info("🚀 Starting Failed Extrinsic Monitor Service...")

    await init_db()
    logger.info("✅ Database initialized")

    monitor = FailedExtrinsicMonitor()

    try:
        await monitor.start()
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down Failed Extrinsic Monitor Service...")
        await monitor.stop()


if __name__ == "__main__":
    asyncio.run(main())
