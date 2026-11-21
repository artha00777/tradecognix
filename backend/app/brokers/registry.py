"""Broker registry: key -> adapter class. SDKs load lazily, so listing works without them."""
from __future__ import annotations

import importlib
from typing import Any

from .base import BrokerAdapter, BrokerError

_REGISTRY: dict[str, tuple[str, str]] = {
    "angelone": ("app.brokers.angelone", "AngelOneAdapter"),
    "zerodha": ("app.brokers.zerodha", "ZerodhaAdapter"),
    "upstox": ("app.brokers.upstox", "UpstoxAdapter"),
    "dhan": ("app.brokers.dhan", "DhanAdapter"),
    "fyers": ("app.brokers.fyers", "FyersAdapter"),
    "groww": ("app.brokers.groww", "GrowwAdapter"),
    "icici": ("app.brokers.icici", "IciciAdapter"),
    "kotak": ("app.brokers.kotak", "KotakNeoAdapter"),
    "fivepaisa": ("app.brokers.fivepaisa", "FivePaisaAdapter"),
    "aliceblue": ("app.brokers.aliceblue", "AliceBlueAdapter"),
    "shoonya": ("app.brokers.shoonya", "ShoonyaAdapter"),
    "paper": ("app.brokers.paper", "PaperBroker"),
}


def adapter_class(key: str) -> type[BrokerAdapter]:
    try:
        mod, cls = _REGISTRY[key]
    except KeyError:
        raise BrokerError(f"Unknown broker '{key}'. Available: {', '.join(sorted(_REGISTRY))}")
    return getattr(importlib.import_module(mod), cls)


def get_adapter(key: str, credentials: dict[str, Any] | None = None, **kw) -> BrokerAdapter:
    return adapter_class(key)(credentials or {}, **kw)


def list_brokers(include_paper: bool = False) -> list[dict]:
    return [adapter_class(k).meta() for k in _REGISTRY if include_paper or k != "paper"]
