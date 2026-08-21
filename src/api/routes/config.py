"""Public config for frontend."""

from fastapi import APIRouter

router = APIRouter()


@router.get("")
async def get_config():
    """Return public client config (no auth)."""
    return {}
