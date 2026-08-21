"""
Initialize Redis with default addresses and types in the new format.

This script updates the Redis database with:
1. default_types: {type_name: color_hex}
2. default_addresses: {address: [label, type]}
"""

import asyncio
import json
import sys
import os

# Add parent directory to path to import project modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.common.cache import get_cache


# Default types with colors
DEFAULT_TYPES = {
    "exchange": "#ff6b6b",  # Red
    "owner": "#4ecdc4",      # Teal
}

# Default addresses with [label, type] format
DEFAULT_ADDRESSES = {
    "5Hd2ze5ug8n1bo3UCAcQsf66VNjKqGos8u6apNfzcU86pg4N": ["Binance1", "exchange"],
    "5GBnPzvPghS8AuCoo6bfnK7JUFHuyUhWSFD4woBNsKnPiEUi": ["Binance2", "exchange"],
    "5FqBL928choLPmeFz5UVAvonBD5k7K2mZSXVC9RkFzLxoy2s": ["MEXC", "exchange"],
    "5HiveMEoWPmQmBAb8v63bKPcFhgTGCmST1TVZNvPHSTKFLCv": ["Taobridge", "exchange"],
    "5CVSCaCXDRydd3Mkdbu2tHLNYxmNTSpy9yRixa1TRGvgRFS8": ["Crypto.com", "exchange"],
    "5CNChyk2fnVgVSZDLAVVFb4QBTMGm6WfuQvseBG6hj8xWzKP": ["Gate.io", "exchange"],
    "5FZiuxCBt8p6PFDisJ9ZEbBaKNVKy6TeemVJd1Z6jscsdjib": ["Kucoin", "exchange"],
    "5HHS55o12rsvH5cKdAYzFW1bsPiQPPctcC8pcJLBHZbZnpHV": ["TAO-Validator", "exchange"],
    "5D1yyu4JrXeCkr4RrFtHbqJNSeKkzNayUFabqMgBu3XFTpcM": ["Spider1", ""],
    "5GzkFjsrmzSX25p26NYLJWtp396DUJEbWfPkyfLgWAgjEt4J": ["Spider2", ""],
}


async def main():
    """Initialize Redis with default addresses and types."""
    print("Initializing Redis with default addresses and types...")
    
    # Get cache instance
    cache = await get_cache()
    
    # Store default types
    types_json = json.dumps(DEFAULT_TYPES)
    await cache.set('default_types', types_json)
    print(f"✅ Stored {len(DEFAULT_TYPES)} default types")
    
    # Store default addresses
    addresses_json = json.dumps(DEFAULT_ADDRESSES)
    await cache.set('default_addresses', addresses_json)
    print(f"✅ Stored {len(DEFAULT_ADDRESSES)} default addresses")
    
    # Verify the data
    stored_types = await cache.get('default_types')
    stored_addresses = await cache.get('default_addresses')
    
    print("\n=== Verification ===")
    print(f"default_types: {stored_types}")
    print(f"default_addresses: {stored_addresses}")
    
    print("\n✅ Initialization complete!")


if __name__ == "__main__":
    asyncio.run(main())
