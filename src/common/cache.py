"""Redis cache connection and utilities."""

import logging
from redis.asyncio import Redis
from redis.exceptions import ReadOnlyError

from src.common.config import settings

logger = logging.getLogger(__name__)

redis_client: Redis | None = None


async def init_cache() -> Redis:
    """Initialize Redis connection with retry on failure."""
    global redis_client
    redis_client = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_keepalive=True,
        health_check_interval=30,
    )
    return redis_client


async def get_cache() -> Redis:
    """Get the Redis client instance."""
    if redis_client is None:
        return await init_cache()
    return redis_client


async def is_redis_readonly() -> bool:
    """Check if Redis is in read-only mode."""
    try:
        cache = await get_cache()
        info = await cache.info("replication")
        # Check if this is a replica or if master is failing
        role = info.get("role", "master")
        if role == "slave":
            logger.warning("Connected to Redis replica instead of master")
            return True
        return False
    except Exception as e:
        logger.error(f"Failed to check Redis read-only status: {e}")
        return False


async def close_cache() -> None:
    """Close Redis connection."""
    global redis_client
    if redis_client is not None:
        await redis_client.close()
        redis_client = None

