# TradeCognix roadmap

## Phase 1 (this repo): foundation

- Designed corporate site + Strategy Studio prototype, wired to a real API.
- Plain-English → strategy DSL (rule-based + optional free LLM), explainer.
- Backtester with Indian costs, whipsaw detection and parameter sweeps.
- Paper + live engine with guardrails, kill switches, order tagging, trade journal.
- Angel One fully integrated; Zerodha, Upstox, Dhan, Fyers best-effort; other brokers as skeletons.
- Admin console; Docker; CI.

## Phase 2: real data, more brokers, multi-strategy

- **Real option history** for backtests (broker historical option candles where available, otherwise licensed EOD/intraday data) to replace the Black-Scholes proxy; India VIX series for IV.
- Finish and certify adapters: Zerodha, Upstox, Dhan, Fyers with real accounts; implement Groww, ICICI Breeze, Kotak Neo, 5paisa, Alice Blue, Shoonya, Motilal Oswal.
- **Broker partnerships / empanelment** under the SEBI retail-algo framework (algo registration, exchange algo IDs) so TradeCognix can be offered to clients through each broker.
- Live futures routing (contract resolver, rollover), basket orders for multi-leg, broker-side SL orders.
- Multiple strategies per user running together, with portfolio-level risk (capital allocation, total daily loss cap, correlated exposure).
- Journal UI with tagging, notes, screenshots, and per-strategy scorecards; CSV/Excel export for tax.
- Strategy versioning: every edit creates a version; live sessions pin a version.
- Alembic migrations, Redis-backed task queue, Prometheus metrics, Sentry.

## Phase 3: mobile and scale

- **Expo React Native app** (see `mobile/README.md`): Studio, backtest results, live monitor, kill switch, push alerts.
- Mobile admin (approvals, kill switch, incident view).
- Multi-tenant white-label deployment, per-tenant branding and domains.
- Horizontal engine workers with leader election; per-broker rate limiting.

## Feature ideas (differentiators)

| Idea | What it does | Why it matters |
|---|---|---|
| **Strategy copilot** | Chat that edits the DSL ("make it exit 15 min earlier", "add a 20% SL"), explains every backtest number, and suggests one change at a time with a before/after backtest. | Turns a one-shot parser into an ongoing coach; drives engagement and backtest usage. |
| **Hinglish / regional-language prompts** | Hindi, Hinglish, Tamil, Telugu, Marathi, Gujarati, Bengali strategy text (basic Hinglish already in the rule parser; full coverage via the LLM path). Voice input on mobile. | Reaches the large non-English-first trader base no Western tool serves. |
| **Market regime detector** | Labels each day/session as trending, range-bound or volatile (ADX, ATR percentile, VIX), shows how each strategy performs per regime, and can pause a strategy in hostile regimes. | Directly attacks the #1 failure mode of crossover systems: chop. |
| **Whipsaw & slippage autopsy** | After every backtest and every live day: which losses were whipsaws, what a cooldown/ADX filter would have saved, how much slippage cost vs the model, best/worst fill times. | Makes the platform feel like it is on the trader's side; concrete, explainable improvements. |
| **Strategy health score** | One 0-100 score from out-of-sample vs in-sample drift, drawdown vs backtest, live vs paper slippage, and regime fit. Alerts when it drops. | A simple signal for when to stop trading something. |
| **One-tap broker switch** | Same strategy, same tags, moved to another connected broker in one tap (adapter abstraction already exists). | No lock-in; resilience when a broker API is down. |
| **Telegram / WhatsApp alerts** | Entry/exit/SL hits, daily P&L, kill-switch confirmations, health-score drops; reply "KILL" to stop everything. Telegram Bot API is free; WhatsApp via Business Platform. | Traders live in these apps; alerts are a habit loop. |
| **Paper-to-live graduation** | A strategy can go live only after N paper days within tolerance of its backtest. | Safety and compliance story for brokers and regulators. |

## Revenue ideas

| Stream | Model |
|---|---|
| **Subscriptions** | Free (paper + limited backtests), Pro ₹999/month or ₹9,990/year (live, sweeps, alerts), Elite (multi-strategy, priority support, portfolio risk). |
| **Backtest credits** | Free monthly credits; pay-as-you-go packs for large sweeps and long real-data histories. |
| **Strategy marketplace** | Creators publish strategies (white-box rules visible, or black-box via SEBI-registered RAs); revenue share on subscriptions. |
| **Copy trading via SEBI-registered RAs** | Users follow strategies from registered Research Analysts; TradeCognix handles execution in each user's own account; RA and platform share fees. Must follow SEBI RA and algo-provider rules. |
| **White-label** | Brokers, sub-brokers and advisory firms license TradeCognix under their brand (setup fee + per-active-user fee). |
| **Broker partnerships** | Referral/revenue share for accounts opened through TradeCognix, and integration fees from brokers that want "plain-English algo" as a feature. |
| **Courses & cohorts** | Paid courses on systematic trading, risk and costs, built on the platform (backtests as homework). |
| ~~Managed VPS / static IP~~ | Dropped: NSE circular NSE/INSP/77559 (20 Nov 2026) bars giving static IPs to clients who don't code their own algos. Replaced by broker-hosted deployment as an empanelled algo provider. |
| **Data add-ons** | Clean option-chain history, VIX, corporate actions, sold as upgrades. |

All client-facing offerings (marketplace, copy trading, white-label) need the SEBI retail-algo framework in place: broker approval and exchange registration of algos, and RA registration for black-box strategies.
