"""Performance metrics and whipsaw detection."""
from __future__ import annotations

from collections import defaultdict

import numpy as np

COST_KEYS = ("brokerage", "stt", "exchange", "sebi", "stamp", "gst")


def detect_whipsaws(trades: list[dict], max_bars: int = 2) -> list[int]:
    """Indices of whipsaw trades: losing trades closed by a signal within `max_bars` bars."""
    out = []
    for k, t in enumerate(trades):
        if t["net_pnl"] < 0 and t["bars_held"] <= max_bars and "signal" in t["exit_reason"]:
            out.append(k)
    return out


def max_drawdown(equity: np.ndarray) -> tuple[float, int, int]:
    """Absolute max drawdown on an equity series, plus peak/trough indices."""
    if len(equity) == 0:
        return 0.0, 0, 0
    peaks = np.maximum.accumulate(equity)
    dd = peaks - equity
    trough = int(np.argmax(dd))
    peak = int(np.argmax(equity[: trough + 1])) if trough > 0 else 0
    return float(dd[trough]), peak, trough


def compute_metrics(trades: list[dict], capital: float = 500000.0, whipsaw_bars: int = 2,
                    session_days: list[str] | None = None) -> tuple[dict, list[dict]]:
    """Trade-list metrics.

    - Breakeven trades (net == 0) are neither wins nor losses (they still count in `trades`).
    - Profit factor with no losing trade is undefined in JSON (infinite): reported as None with
      `profit_factor_display` = "∞" so the UI can tell it apart from "no trades".
    - Sharpe uses daily net P&L / capital over *every* session day in `session_days` (days
      without trades count as 0), annualised with sqrt(252). Without `session_days` only days
      with trades are used (overstates Sharpe; kept for ad-hoc callers).
    - Max drawdown % is measured against the running peak of account equity (capital + P&L).
    """
    n = len(trades)
    nets = np.array([t["net_pnl"] for t in trades], dtype=float)
    gross = np.array([t["gross_pnl"] for t in trades], dtype=float)
    wins = nets[nets > 0]
    losses = nets[nets < 0]
    breakeven = int(np.sum(nets == 0)) if n else 0
    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())
    cum = np.cumsum(nets) if n else np.array([])
    curve = np.concatenate([[0.0], cum])
    mdd, peak_i, trough_i = max_drawdown(curve)
    peak_equity = capital + float(curve[peak_i]) if len(curve) else capital
    charges = defaultdict(float)
    slippage = 0.0
    for t in trades:
        for k in COST_KEYS:
            charges[k] += t["charges"].get(k, 0.0)
        slippage += t["slippage"]
    # consecutive losses
    max_consec = cur = 0
    for v in nets:
        cur = cur + 1 if v < 0 else 0
        max_consec = max(max_consec, cur)
    # daily stats
    daily = defaultdict(float)
    for t in trades:
        daily[t["date"]] += t["net_pnl"]
    dvals = np.array(list(daily.values()), dtype=float)
    if session_days:
        all_days = sorted(set(session_days) | set(daily))
        series = np.array([daily.get(d, 0.0) for d in all_days], dtype=float)
    else:
        series = dvals
    rets = series / capital if capital else series
    sd = float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
    sharpe = float(rets.mean() / sd * np.sqrt(252)) if sd > 1e-12 else 0.0
    downside = rets[rets < 0]
    dsd = float(np.sqrt(np.mean(downside ** 2))) if len(downside) else 0.0
    sortino = float(rets.mean() / dsd * np.sqrt(252)) if dsd > 1e-12 else None
    whips = detect_whipsaws(trades, whipsaw_bars)
    exit_reasons = defaultdict(int)
    for t in trades:
        exit_reasons[t["exit_reason"]] += 1
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    total_costs = sum(charges.values()) + slippage
    if gross_loss > 0:
        pf, pf_display = round(gross_profit / gross_loss, 2), None
    elif gross_profit > 0:
        pf, pf_display = None, "∞"  # winners only
    else:
        pf, pf_display = None, None  # no trades / only breakevens
    metrics = {
        "trades": n,
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "breakeven": breakeven,
        "win_rate": round(len(wins) / n * 100, 2) if n else 0.0,
        "gross_pnl": round(float(gross.sum()), 2),
        "net_pnl": round(float(nets.sum()), 2),
        "total_costs": round(total_costs, 2),
        "cost_breakdown": {**{k: round(v, 2) for k, v in charges.items()}, "slippage": round(slippage, 2)},
        "costs_pct_of_gross": round(total_costs / abs(gross.sum()) * 100, 2) if n and gross.sum() != 0 else None,
        "profit_factor": pf,
        "profit_factor_display": pf_display if pf_display else (f"{pf:.2f}" if pf is not None else "–"),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "payoff_ratio": round(avg_win / abs(avg_loss), 2) if avg_loss else None,
        "expectancy": round(float(nets.mean()), 2) if n else 0.0,
        "max_drawdown": round(mdd, 2),
        "max_drawdown_pct": round(mdd / peak_equity * 100, 2) if peak_equity > 0 else None,
        "return_pct": round(float(nets.sum()) / capital * 100, 2) if capital else None,
        "max_consecutive_losses": int(max_consec),
        "trading_days": len(daily),
        "session_days": len(session_days) if session_days else len(daily),
        "best_day": round(float(dvals.max()), 2) if len(dvals) else 0.0,
        "worst_day": round(float(dvals.min()), 2) if len(dvals) else 0.0,
        "sharpe_daily_annualised": round(sharpe, 2),
        "sortino_daily_annualised": round(sortino, 2) if sortino is not None else None,
        "avg_bars_held": round(float(np.mean([t["bars_held"] for t in trades])), 2) if n else 0.0,
        "exit_reasons": dict(exit_reasons),
        "whipsaw": {
            "count": len(whips),
            "pct_of_trades": round(len(whips) / n * 100, 2) if n else 0.0,
            "net_loss": round(float(sum(trades[k]["net_pnl"] for k in whips)), 2),
            "definition": f"losing trade closed by a signal within {whipsaw_bars} bars",
            "trade_indices": whips,
        },
    }
    equity = [{"t": t["exit_time"], "equity": round(float(c), 2)} for t, c in zip(trades, cum)]
    return metrics, equity
