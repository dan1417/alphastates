"""Subtensor connection pool for fast concurrent queries."""

import asyncio
import logging
from typing import Optional
import bittensor as bt
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class SubtensorPool:
    """Pool of subtensor connections for concurrent queries."""
    
    def __init__(self, rpc_url: str, pool_size: int = 50):
        """Initialize the subtensor pool.
        
        Args:
            rpc_url: WebSocket RPC URL for the subtensor node
            pool_size: Number of connections to maintain in the pool
        """
        self.rpc_url = rpc_url
        self.pool_size = pool_size
        self.pool: asyncio.Queue = asyncio.Queue(maxsize=pool_size)
        self._initialized = False
        self._lock = asyncio.Lock()
        
    async def initialize(self):
        """Initialize all connections in the pool."""
        if self._initialized:
            logger.warning("Subtensor pool already initialized")
            return
            
        logger.info(f"Initializing subtensor pool with {self.pool_size} connections...")
        
        # Create connections in parallel for faster startup
        tasks = []
        for i in range(self.pool_size):
            tasks.append(self._create_connection(i))
        
        connections = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Add successful connections to the pool
        success_count = 0
        for i, conn in enumerate(connections):
            if isinstance(conn, Exception):
                logger.error(f"Failed to create connection {i}: {conn}")
            else:
                await self.pool.put(conn)
                success_count += 1
        
        self._initialized = True
        logger.info(f"Subtensor pool initialized with {success_count}/{self.pool_size} connections")
        
    async def _create_connection(self, index: int) -> bt.Subtensor:
        """Create a single subtensor connection.
        
        Args:
            index: Connection index for logging
            
        Returns:
            Initialized subtensor instance
        """
        loop = asyncio.get_event_loop()
        # Run the blocking subtensor initialization in a thread pool
        subtensor = await loop.run_in_executor(None, bt.Subtensor, self.rpc_url)
        logger.debug(f"Created subtensor connection {index}")
        return subtensor
    
    @asynccontextmanager
    async def get_connection(self):
        """Get a subtensor connection from the pool.
        
        Usage:
            async with pool.get_connection() as subtensor:
                result = await subtensor.some_method()
        
        Yields:
            subtensor instance from the pool
        """
        if not self._initialized:
            raise RuntimeError("Subtensor pool not initialized. Call initialize() first.")
        
        # Get a connection from the pool (blocks if all are in use)
        subtensor = await self.pool.get()
        try:
            yield subtensor
        finally:
            # Return the connection to the pool
            await self.pool.put(subtensor)
    
    async def close(self):
        """Close all connections in the pool."""
        logger.info("Closing subtensor pool...")
        
        closed_count = 0
        while not self.pool.empty():
            try:
                subtensor = self.pool.get_nowait()

                if subtensor is not None:
                    try:
                        # Close the substrate connection if it exists
                        if hasattr(subtensor, 'substrate') and subtensor.substrate:
                            # Close websocket connection
                            if hasattr(subtensor.substrate, 'websocket') and subtensor.substrate.websocket:
                                subtensor.substrate.websocket.close()
                            # Close the substrate itself
                            subtensor.substrate.close()
                    except Exception as e:
                        pass  # Ignore cleanup errors
                    finally:
                        subtensor = None
                        
                closed_count += 1
            except asyncio.QueueEmpty:
                break
        
        logger.info(f"Closed {closed_count} subtensor connections")
        self._initialized = False
    
    def is_initialized(self) -> bool:
        """Check if the pool is initialized."""
        return self._initialized
    
    def available_connections(self) -> int:
        """Get the number of available connections in the pool."""
        return self.pool.qsize()


# Global pool instance
_subtensor_pool: Optional[SubtensorPool] = None


async def init_subtensor_pool(rpc_url: str, pool_size: int = 50):
    """Initialize the global subtensor pool.
    
    Args:
        rpc_url: WebSocket RPC URL for the subtensor node
        pool_size: Number of connections to maintain in the pool
    """
    global _subtensor_pool
    
    if _subtensor_pool is not None:
        logger.warning("Subtensor pool already exists")
        return
    
    _subtensor_pool = SubtensorPool(rpc_url, pool_size)
    await _subtensor_pool.initialize()


async def close_subtensor_pool():
    """Close the global subtensor pool."""
    global _subtensor_pool
    
    if _subtensor_pool is not None:
        await _subtensor_pool.close()
        _subtensor_pool = None


def get_subtensor_pool() -> SubtensorPool:
    """Get the global subtensor pool instance.
    
    Returns:
        The global SubtensorPool instance
        
    Raises:
        RuntimeError: If the pool has not been initialized
    """
    if _subtensor_pool is None:
        raise RuntimeError("Subtensor pool not initialized. Call init_subtensor_pool() first.")
    return _subtensor_pool
