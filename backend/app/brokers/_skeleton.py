"""Shared base for documented-but-unimplemented broker adapters."""
from __future__ import annotations

from .base import BrokerAdapter, BrokerNotImplemented


class SkeletonAdapter(BrokerAdapter):
    status = "skeleton"
    sdk_hint = ""

    def _ni(self, what: str):
        raise BrokerNotImplemented(
            f"{self.display_name} ({self.api_name}) adapter is a skeleton: '{what}' is not implemented yet. "
            f"SDK/API: {self.sdk_hint}. Docs: {self.docs_url}"
        )

    def login(self):
        self._ni("login")

    def profile(self):
        self._ni("profile")

    def funds(self):
        self._ni("funds")

    def historical_candles(self, instrument, timeframe, start, end):
        self._ni("historical_candles")

    def ltp(self, instrument):
        self._ni("ltp")

    def search_instruments(self, query, exchange="NSE"):
        self._ni("search_instruments")

    def place_order(self, req):
        self._ni("place_order")

    def cancel_order(self, order_id):
        self._ni("cancel_order")

    def positions(self):
        self._ni("positions")

    def order_book(self):
        self._ni("order_book")
