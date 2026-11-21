"""Risk guardrails shared by paper and live sessions."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings


class GuardrailError(PermissionError):
    pass


LIVE_CONFIRM_PHRASE = "LIVE"


def assert_live_allowed(settings: Settings, strategy_live_confirmed: bool, confirm_text: str | None, broker_key: str, broker_status: str) -> None:
    """Live orders need ALL of: server flag, per-strategy confirmation, typed confirmation, a real broker."""
    if not settings.live_trading_enabled:
        raise GuardrailError("Live trading is disabled on this server (set LIVE_TRADING_ENABLED=true after testing on paper).")
    if not strategy_live_confirmed:
        raise GuardrailError("This strategy has not been confirmed for live trading. Confirm it first (POST /api/strategies/{id}/confirm-live).")
    if (confirm_text or "").strip() != LIVE_CONFIRM_PHRASE:
        raise GuardrailError(f"Type {LIVE_CONFIRM_PHRASE} to confirm real-money trading.")
    if broker_key == "paper":
        raise GuardrailError("Live mode needs a connected broker account, not the paper broker.")
    if broker_status == "skeleton":
        raise GuardrailError(f"The {broker_key} adapter is not implemented yet; use paper mode.")


@dataclass
class Guardrails:
    max_trades_per_day: int
    daily_loss_cap: float | None
    hard_exit: str  # HH:MM
    entry_start: str
    entry_end: str

    def entry_block_reason(self, now_hhmm: str, trades_today: int, pnl_today: float, halted: bool, kill_switch: bool) -> str | None:
        if kill_switch:
            return "kill_switch"
        if halted:
            return "halted"
        if trades_today >= self.max_trades_per_day:
            return "max_trades_reached"
        if self.daily_loss_cap and pnl_today <= -self.daily_loss_cap:
            return "daily_loss_cap"
        if now_hhmm >= self.hard_exit:
            return "after_hard_exit"
        if not (self.entry_start <= now_hhmm <= self.entry_end):
            return "outside_entry_window"
        return None

    def flatten_reason(self, now_hhmm: str, pnl_today_incl_open: float, kill_switch: bool) -> str | None:
        if kill_switch:
            return "kill_switch"
        if now_hhmm >= self.hard_exit:
            return "hard_exit"
        if self.daily_loss_cap and pnl_today_incl_open <= -self.daily_loss_cap:
            return "daily_loss_cap"
        return None
