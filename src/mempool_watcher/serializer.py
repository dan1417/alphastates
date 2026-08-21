"""Serialize decoded substrate calls to JSON-safe dicts for WebSocket clients."""

import time
from typing import Any, Dict, List, Tuple


def _to_serializable(value: Any) -> Any:
    """Convert substrate/Scale types to JSON-serializable values."""
    if value is None:
        return None
    if hasattr(value, "value"):
        return _to_serializable(value.value)
    if hasattr(value, "to_hex"):
        return value.to_hex()
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_to_serializable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_serializable(v) for k, v in value.items()}
    return str(value)


def get_call_args_dict(decoded_call: dict) -> Dict[str, Any]:
    """Turn call_args into a simple name -> serializable value dict."""
    call_args = decoded_call.get("call_args", [])
    out: Dict[str, Any] = {}
    if isinstance(call_args, dict):
        for name, val in call_args.items():
            out[name] = _to_serializable(val.value if hasattr(val, "value") else val)
    else:
        for arg in call_args:
            if isinstance(arg, dict):
                out[arg.get("name", "?")] = _to_serializable(arg.get("value", arg))
            else:
                name = getattr(arg, "name", str(arg))
                out[name] = _to_serializable(getattr(arg, "value", arg))
    return out


# Only these are forwarded to the frontend (add stake, remove stake, transfer)
DISPLAY_ADD_STAKE = "add_stake"
DISPLAY_REMOVE_STAKE = "remove_stake"
DISPLAY_TRANSFER = "transfer"
DISPLAY_TRANSFER_STAKE = "transfer_stake"
DISPLAY_MOVE_STAKE = "move_stake"
DISPLAY_SWAP_STAKE = "swap_stake"
DISPLAY_START_CALL = "start_call"
DISPLAY_EVM = "evm"
DISPLAY_MEVSHIELD = "mevshield"
DISPLAY_SYSTEM = "system"


def _ensure_dict(obj: Any) -> dict:
    """Convert substrate value to dict if needed."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "value"):
        return _ensure_dict(obj.value)
    if hasattr(obj, "__getitem__") and "call_module" in dir(obj):
        return {k: getattr(obj, k, None) for k in ("call_module", "call_function", "call_args")}
    return {}


def get_display_type(module: str, function: str) -> str | None:
    """Map call_module/call_function to display_type or None if not interested."""
    if module == "SubtensorModule":
        if function in ("add_stake_limit", "add_stake"):
            return DISPLAY_ADD_STAKE
        if function in ("remove_stake", "remove_stake_limit", "remove_stake_full_limit"):
            return DISPLAY_REMOVE_STAKE
        if function in ("transfer_stake"):
            return DISPLAY_TRANSFER_STAKE
        if function in ("move_stake"):
            return DISPLAY_MOVE_STAKE
        if function in ("swap_stake", "swap_stake_limit"):
            return DISPLAY_SWAP_STAKE
        if function in ("start_call"):
            return DISPLAY_START_CALL
    if module == "Ethereum" and function == "transact":
        return DISPLAY_EVM
    if module == "Balances":
        if function in ("force_transfer", "transfer_all", "transfer_allow_death", "transfer_keep_alive"):
            return DISPLAY_TRANSFER
    if module == "MevShield":
        if function in ("announce_next_key", "submit_encrypted"):
            return DISPLAY_MEVSHIELD
    if module == "System":
        if function in ("set_heap_pages"):
            return DISPLAY_SYSTEM
    return None


def extract_interested_calls(
    address: str, call: dict, proxy_address: str = ""
) -> List[Tuple[str, dict, str, str]]:
    """
    Extract (address, call_dict, display_type, proxy_address) for interested calls.
    Unwraps batch and proxy. For Ethereum.transact, address is replaced by the
    recovered EVM sender (SS58). proxy_address is the on-chain proxy account (the
    original tx signer) when the call was submitted via Proxy.proxy; empty string
    for direct calls.
    """
    call_module = call.get("call_module", "")
    call_function = call.get("call_function", "")

    display_type = get_display_type(call_module, call_function)
    if display_type is not None:
        if display_type == DISPLAY_EVM:
            args = get_call_args_dict(call)
            tx_value = args.get("transaction")
            if isinstance(tx_value, dict):
                tx_dict = tx_value
            else:
                tx_dict = _to_serializable(tx_value) if tx_value is not None else None
            if tx_dict is not None:
                try:
                    from .evm import recover_ethereum_sender_from_transaction, h160_to_ss58
                    evm_sender = recover_ethereum_sender_from_transaction(tx_dict)
                    if evm_sender:
                        address = h160_to_ss58(evm_sender, 42)
                except Exception:
                    pass
            return [(address or "", call, display_type, proxy_address)]
        return [(address, call, display_type, proxy_address)]

    out: List[Tuple[str, dict, str, str]] = []
    call = _ensure_dict(call)
    if call_module == "Utility" and call_function in ("batch", "batch_all", "force_batch"):
        args = get_call_args_dict(call)
        for c in args.get("calls") or []:
            out.extend(extract_interested_calls(address, _ensure_dict(c), proxy_address))
    elif call_module == "Proxy" and call_function == "proxy":
        args = get_call_args_dict(call)
        real = args.get("real")
        proxy_call = args.get("call")
        if real is not None and proxy_call is not None:
            real_addr = _to_serializable(real) if not isinstance(real, str) else real
            out.extend(
                extract_interested_calls(str(real_addr), _ensure_dict(proxy_call), address)
            )
    return out


def build_pending_message(
    ext_id: str,
    address: str,
    call: dict,
    display_type: str,
    proxy_address: str = "",
    proxy_valid: bool | None = None,
) -> dict:
    """Build one JSON-serializable message for a pending extrinsic.

    proxy_address: the on-chain proxy account (tx signer) when submitted via
        Proxy.proxy; empty string for direct calls.
    proxy_valid: True/False when the proxy relationship was checked against the
        cache; None when not applicable (direct call) or cache unavailable.
    """
    args = get_call_args_dict(call)
    msg = {
        "type": "pending_extrinsic",
        "id": ext_id,
        "received_at": int(time.time()),
        "address": _to_serializable(address) if not isinstance(address, str) else address,
        "call_module": call.get("call_module", ""),
        "call_function": call.get("call_function", ""),
        "display_type": display_type,
        "args": args,
    }
    if proxy_address:
        msg["proxy_address"] = proxy_address
        msg["proxy_valid"] = proxy_valid
    if display_type == DISPLAY_EVM:
        tx_raw = args.get("transaction")
        msg["evm_transaction"] = _to_serializable(tx_raw) if tx_raw is not None else None
    return msg
