"""Standalone Event Monitor Service."""

import asyncio
import logging

from src.common.cache import init_cache
from src.event_monitor.monitor import EventMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    """Run the event monitor service."""
    logger.info("🚀 Starting Event Monitor Service...")
    
    # Initialize Redis
    redis_client = await init_cache()
    logger.info("✅ Connected to Redis")
    
    # Start the blockchain monitor
    monitor = EventMonitor(redis_client)
    logger.info("✅ Blockchain monitor started")
    
    # Keep running
    try:
        await monitor.start()
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down Event Monitor Service...")
        await monitor.stop()


if __name__ == "__main__":
    asyncio.run(main())

