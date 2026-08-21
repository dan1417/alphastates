"""EVM transaction helpers: recover sender from EIP-1559 tx, H160 to SS58."""

import hashlib
from typing import Any, Dict

try:
    import rlp
    from eth_keys import keys
    from eth_hash.auto import keccak
    import scalecodec
    _EVM_AVAILABLE = True
except ImportError:
    _EVM_AVAILABLE = False


def _hex_to_bytes(h: Any) -> bytes:
    """Normalize hex string (with or without 0x) to bytes."""
    if isinstance(h, bytes):
        return h
    s = str(h).strip()
    if s.startswith("0x"):
        s = s[2:]
    return bytes.fromhex(s) if s else b""


def _eip1559_access_list_to_rlp(access_list: list) -> list:
    """Convert decoded access_list to RLP form."""
    if not access_list:
        return []
    out = []
    for item in access_list:
        if isinstance(item, dict):
            addr = _hex_to_bytes(item.get("address", item.get("address_bytes", b"")))
            keys_list = item.get("storage_keys", item.get("storage_keys_list", []))
            slots = []
            for k in keys_list:
                if isinstance(k, (list, tuple)) and len(k) >= 2:
                    slots.append([_hex_to_bytes(k[0]), _hex_to_bytes(k[1])])
                else:
                    slots.append([_hex_to_bytes(k), b""])
            out.append([addr, slots])
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append([
                _hex_to_bytes(item[0]),
                [[_hex_to_bytes(s[0]), _hex_to_bytes(s[1]) if len(s) > 1 else b""] for s in item[1]],
            ])
    return out


def recover_ethereum_sender_from_transaction(transaction_value: Dict[str, Any]) -> str | None:
    """
    Recover the Ethereum 'from' address from a decoded EIP-1559 transaction.
    transaction_value: dict with "EIP1559" key (e.g. from call_args['transaction']).
    Returns checksum address string or None on failure.
    """
    if not _EVM_AVAILABLE:
        return None
    try:
        if not isinstance(transaction_value, dict) or "EIP1559" not in transaction_value:
            return None
        tx = transaction_value["EIP1559"]
        chain_id = tx.get("chain_id", 0)
        nonce = tx.get("nonce", 0)
        max_priority_fee_per_gas = tx.get("max_priority_fee_per_gas", 0)
        max_fee_per_gas = tx.get("max_fee_per_gas", 0)
        gas_limit = tx.get("gas_limit", 0)
        action = tx.get("action", {})
        value = tx.get("value", 0)
        data = _hex_to_bytes(tx.get("input", tx.get("data", "0x")))
        access_list = tx.get("access_list", [])
        sig = tx.get("signature", {})

        if isinstance(action, dict) and "Call" in action:
            to = _hex_to_bytes(action["Call"])
            if len(to) != 20:
                to = to.rjust(20, b"\x00") if len(to) < 20 else to[-20:]
        elif isinstance(action, dict) and "Create" in action:
            to = b""
        else:
            to = b""

        access_list_rlp = _eip1559_access_list_to_rlp(access_list)
        unsigned_list = [
            chain_id,
            nonce,
            max_priority_fee_per_gas,
            max_fee_per_gas,
            gas_limit,
            to,
            value,
            data,
            access_list_rlp,
        ]
        payload = b"\x02" + rlp.encode(unsigned_list)
        message_hash = keccak(payload)

        v = 1 if sig.get("odd_y_parity", False) else 0
        r = int(sig.get("r", "0x0"), 16)
        s = int(sig.get("s", "0x0"), 16)
        signature = keys.Signature(vrs=(v, r, s))
        public_key = signature.recover_public_key_from_msg_hash(message_hash)
        return public_key.to_checksum_address()
    except Exception:
        return None


def h160_to_ss58(h160_address: str, valid_ss58_format: int = 42) -> str:
    """Convert Ethereum 20-byte address (hex or 0x-prefixed) to substrate SS58 (evm: prefix)."""
    if not _EVM_AVAILABLE:
        return h160_address
    if isinstance(h160_address, str) and h160_address.startswith("0x"):
        h160_address = h160_address[2:]
    address_bytes = bytes.fromhex(h160_address)
    prefixed_address = b"evm:" + address_bytes
    checksum = hashlib.blake2b(prefixed_address, digest_size=32).digest()
    return scalecodec.ss58_encode(checksum, ss58_format=valid_ss58_format)
