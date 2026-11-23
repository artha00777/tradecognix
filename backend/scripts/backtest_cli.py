"""Backtest a plain-English strategy from the command line (synthetic data or a CSV).

    python -m scripts.backtest_cli "Buy Nifty ATM call when 9 EMA crosses above 21 EMA on 15-min candles after 9:30 AM. Exit on opposite cross or 3:15 PM. Max 2 trades a day."
    python -m scripts.backtest_cli "<text>" --csv data/nifty_5m_synthetic.csv --sweep
"""
from __future__ import annotations

import argparse
import json

from app.backtest.costs import CostConfig
from app.backtest.engine import run_backtest
from app.backtest.sweep import sweep
from app.data.candles import read_csv, resample
from app.data.synthetic import generate_candles
from app.strategy.explain import explain
from app.strategy.parser import parse_strategy


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--csv")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--slippage", type=float, default=0.5)
    ap.add_argument("--sweep", action="store_true", help="timeframes x modes x entry times grid")
    a = ap.parse_args()
    res = parse_strategy(a.text)
    s = res.strategy
    print("Parsed:", explain(s))
    for w in res.warnings:
        print("  note:", w)
    from app.data.candles import synthetic_bar_minutes

    base = read_csv(a.csv) if a.csv else generate_candles(s.underlying, days=a.days, bar_minutes=synthetic_bar_minutes(s.timeframe))
    source = "csv" if a.csv else "synthetic"
    cfg = CostConfig(slippage_pct=a.slippage)
    if a.sweep:
        rows = sweep(s, base, ["5m", "15m", "30m"], ["atm_buy", "itm_buy", "atm_sell"], ["09:30", "09:45", "10:00"], cfg, source)
        print(f"{'tf':>4} {'mode':>9} {'start':>6} {'trades':>6} {'win%':>6} {'net':>12} {'maxDD':>10} {'PF':>5} {'whip':>4}")
        for r in rows:
            if "error" in r:
                print(r)
                continue
            print(f"{r['timeframe']:>4} {str(r['mode']):>9} {r['entry_start']:>6} {r['trades']:>6} {r['win_rate']:>6} {r['net_pnl']:>12,.0f} {r['max_drawdown']:>10,.0f} {str(r['profit_factor']):>5} {r['whipsaws']:>4}")
        return
    out = run_backtest(s, resample(base, s.timeframe), source, cost_cfg=cfg)
    m = out.metrics
    keys = ["trades", "win_rate", "gross_pnl", "net_pnl", "total_costs", "profit_factor", "payoff_ratio", "expectancy", "max_drawdown", "max_consecutive_losses"]
    print(json.dumps({k: m[k] for k in keys}, indent=2))
    print("costs:", m["cost_breakdown"])
    print("whipsaws:", {k: v for k, v in m["whipsaw"].items() if k != "trade_indices"})
    print("flags:", out.flags)


if __name__ == "__main__":
    main()
