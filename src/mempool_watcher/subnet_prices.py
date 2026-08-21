"""
Subnet prices loop: wait for each new block, fetch subnet prices (TAO per alpha in rao),
publish to Redis so frontend can convert alpha amounts to TAO for remove_stake / transfer_stake.
"""

import json
import logging
import time
from typing import Callable

logger = logging.getLogger(__name__)


def run_subnet_prices_loop(
    rpc_url: str,
    publish: Callable[[dict], None],
    poll_interval: float = 6.0,
):
    """
    Run forever: on each new block, fetch subnet prices and call publish({ type: 'subnet_prices', block_number, prices: { netuid: price_rao } }).
    prices[netuid] is in rao (TAO rao per alpha); frontend uses (alpha_amount * price_rao) / 1e18 for TAO value.
    """
    try:
        import bittensor as bt
    except ImportError:
        logger.warning("bittensor not installed; subnet prices will not be sent")
        return

    last_block = 0
    subtensor = None

    while True:
        try:
            if subtensor is None:
                subtensor = bt.Subtensor(network=rpc_url)
                logger.info("Subnet prices: connected to %s", rpc_url)

            current_block = subtensor.get_current_block()
            if current_block != last_block and current_block > 0:
                try:
                    prices = subtensor.get_subnet_prices(block=current_block)
                    if prices is not None:
                        # price is Balance; .rao gives int. Keys as str for JSON.
                        prices_rao = {}
                        for netuid, balance in prices.items():
                            rao = getattr(balance, "rao", balance)
                            if hasattr(rao, "value"):
                                rao = rao.value
                            prices_rao[str(netuid)] = int(rao)
                        publish({
                            "type": "subnet_prices",
                            "block_number": current_block,
                            "prices": prices_rao,
                        })
                        logger.debug("Published subnet_prices for block %s (%s subnets)", current_block, len(prices_rao))
                except Exception as e:
                    logger.warning("Failed to get subnet prices for block %s: %s", current_block, e)
                last_block = current_block
        except Exception as e:
            logger.warning("Subnet prices loop error: %s", e)
            subtensor = None

        time.sleep(poll_interval)
