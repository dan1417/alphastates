"""Main entry point for the OHLC indexer."""

import asyncio
import logging
import signal
import sys

from src.common.cache import close_cache, init_cache
from src.common.config import settings
from src.common.db import close_db, get_session, init_db
from src.indexers.ohlc.processor import BlockchainProcessor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

shutdown_event = asyncio.Event()

def handle_shutdown(sig: signal.Signals) -> None:
    """Handle shutdown signals gracefully."""
    logger.info(f"Received signal {sig.name}, shutting down...")
    shutdown_event.set()


async def main() -> None:
    """Main async entry point."""
    logger.info("Starting OHLC Indexer...")

    # Initialize connections
    logger.info("Initializing database...")
    await init_db()

    # logger.info("Initializing cache...")
    # await init_cache()

    try:
        # Initialize processor with proper session management
        async for session in get_session():
            processor = BlockchainProcessor(session)
            await processor.initialize()

            # Setup signal handlers for graceful shutdown
            def signal_handler(sig: int) -> None:
                logger.info("Received shutdown signal")
                asyncio.create_task(processor.stop())
            
            signal.signal(signal.SIGINT, lambda s, f: signal_handler(s))
            signal.signal(signal.SIGTERM, lambda s, f: signal_handler(s))
            
            # Reset database if enabled
            if settings.reset_db:
                await processor.reset_database()
            
            await processor.run()

            logger.info("Indexer stopped.")
            break  # Exit the async for loop after processing

    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        raise
    finally:
        logger.info("Cleaning up...")
        await close_cache()
        await close_db()
        logger.info("Shutdown complete.")


def run() -> None:
    """Synchronous entry point."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Register signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: handle_shutdown(s))

    try:
        loop.run_until_complete(main())
    finally:
        loop.close()


if __name__ == "__main__":
    run()

