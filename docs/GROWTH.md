# TradeCognix: growth and revenue plan

The product's real moat is **plain-English (and Hinglish) strategy → honest backtest with real Indian costs → paper → live on your own broker**, with guardrails. Everything below is built to monetise that without breaking SEBI's retail-algo rules (applicable from 1 April 2026).

## 1. Positioning in one line

> "Say your strategy. See what it really would have made after STT, GST and slippage. Then let it run — with a kill switch."

Lead with **honesty about costs and whipsaws**. Most Indian algo tools sell dreams; TradeCognix shows net P&L after every charge. That is the trust wedge.

## 2. Pricing (enforced server-side via `plan` on User)

| Plan | Price | Who it's for | Limits |
|---|---|---|---|
| Free | ₹0 | Curious traders, students | Paper only, 5 backtests/day, 1 strategy, synthetic + limited history |
| Pro | ₹999/mo or ₹9,990/yr (2 months free) | Active retail traders | Live trading, parameter sweeps, Telegram alerts, 5 strategies |
| Elite | ₹2,499/mo | Serious/part-time pros | Unlimited strategies, priority support, portfolio-level risk, daily P&L digests |

Rules of thumb:
- Default the toggle to **yearly** on the pricing page; annual plans cut churn roughly in half for trading tools.
- Offer a **7-day Pro trial** that starts only after the user's first successful backtest (activation first, then paywall).
- Payments: **Razorpay** (UPI AutoPay for subscriptions, cards, netbanking). Checkout stub is in the backend; add keys to go live. Add GST invoicing (18%) from day one.

## 3. Revenue streams, in the order to build them

| # | Stream | Effort | Time to first ₹ | Notes |
|---|---|---|---|---|
| 1 | **Pro/Elite subscriptions** | Low (done in code) | 2–4 weeks | Needs Razorpay keys, GST registration, Terms/Privacy/Refund pages |
| 2 | **Broker-hosted live trading (empanelled algo provider)** | High | 3–6 months | Replaces the dropped managed static-IP idea (see section 9). Get empanelled with NSE through a partner broker and deploy strategies on the broker's servers. Unlocks live trading for every plain-English user and is the real moat |
| 3 | **Broker referral / partnerships** | Low–medium | 1–3 months | Affiliate payouts for accounts opened via your link (Angel One, Upstox, Dhan, Fyers all run partner programs). Also the path to broker empanelment |
| 4 | **Backtest credit packs** | Low | 1 month | ₹199 for 50 deep backtests on real option history once data is licensed |
| 5 | **Courses & cohorts** | Low | 1 month | "Systematic intraday options in 4 weeks" with backtests as homework. ₹2,999–₹9,999. Builds the audience that converts to Pro |
| 6 | **White-label for brokers, sub-brokers, RIAs/RAs** | High | 3–6 months | Setup ₹2–5 lakh + per-active-user fee. Your strongest B2B story under the new SEBI framework |
| 7 | **Strategy marketplace / copy via SEBI-registered RAs** | High + compliance | 6+ months | Revenue share. Only with registered RAs for black-box strategies; white-box rules can be shared freely |

## 4. Funnel and activation

1. **Landing → Studio with no signup.** Visitor types a strategy and sees a backtest in under 10 seconds (public parse + synthetic backtest endpoints already exist).
2. **Gate the second action, not the first.** Saving a strategy, paper trading, or a real-data backtest asks for an account (Google sign-in recommended).
3. **Activation metric:** first paper session started within 24 hours of signup.
4. **Paywall moment:** going live, running a sweep, or adding a 2nd strategy.
5. **Habit loop:** Telegram daily P&L digest at 3:35 PM IST + "reply KILL to stop". Build this before the mobile app.

## 5. Acquisition (cheap, India-specific)

- **SEO landing pages** per strategy template: "Nifty 9/21 EMA crossover backtest", "BankNifty short straddle 9:20 backtest", "Supertrend strategy Nifty options". Each page = live backtest widget + net-of-cost results. These keywords have real search volume and weak competition.
- **YouTube/Instagram finfluencer collaborations** — but only educational content with the risk disclaimer; SEBI restricts finfluencers tied to regulated entities, so pay for tool demos, never for return claims.
- **Telegram/Discord community** with a weekly "strategy autopsy" (why a popular strategy fails after costs).
- **Hinglish prompts** as a headline feature; nobody else does it well.
- **Referral:** give one month of Pro to both sides when a referred user activates (referral code is on the User model).

## 6. What to build next (highest impact first)

1. Real option premium history (replace Black-Scholes proxy) — credibility of every backtest depends on it.
2. Telegram alerts + kill via chat.
3. Empanelment + broker-hosted deployment with one partner broker (start with whichever of Angel One, Upstox, Dhan or Fyers offers an algo-provider program).
4. Strategy copilot: "make it exit 15 min earlier" edits with before/after backtest.
5. Market-regime filter and whipsaw autopsy (already designed in ROADMAP.md).
6. Mobile app (Expo) with live monitor + kill switch.

## 7. Compliance checklist before charging anyone

- [ ] Company entity + GST registration; Razorpay KYC.
- [ ] Terms of Service, Privacy Policy (DPDP Act 2023), Refund Policy, Risk Disclosure pages.
- [ ] Exchange empanelment as an algo provider through at least one partner broker, with strategies hosted on the broker's servers, before offering live trading to the public.
- [ ] Never provide or arrange static IPs for users who don't code their own algos (NSE/INSP/77559).
- [ ] No return promises anywhere in marketing; show the SEBI F&O loss-study disclaimer.
- [ ] If any strategy is sold as black-box: SEBI Research Analyst registration.

## 8. Metrics to watch weekly

Visitors → Studio runs → signups → first paper session → paid. Plus MRR, annual-plan share, 30-day churn, live sessions halted by guardrails (a safety metric worth publishing).

## Rough revenue model (illustrative, not a forecast)

| Paying users | Mix (Pro/Elite) | Approx. MRR |
|---|---|---|
| 200 | 85% / 15% | ~₹2.4 lakh |
| 1,000 | 85% / 15% | ~₹12 lakh |
| 5,000 | 85% / 15% | ~₹61 lakh |

Plus referral, courses and white-label on top. Assumes monthly pricing; annual plans lower headline MRR but improve retention and cash flow.

## 9. NSE circular of 20 Nov 2026 (NSE/INSP/77559): what changed

- **What it says:** static IPs are only for tech-savvy retail investors who write their own algos, backed by a signed undertaking taken by the broker. Vendor and no-code algos must run on the broker's servers via an empanelled algo provider. Providers that hand out static IPs to ordinary clients risk losing empanelment; brokers face ₹5,000 per client (max ₹1 lakh) for missing undertakings. Effective immediately.
- **Impact on TradeCognix:** its users describe strategies in plain English, so they are not tech-savvy by this definition. The planned **managed static-IP server / VPS add-on is dropped**, and the Elite plan no longer promises it.
- **New live-trading path:** become an empanelled algo provider via partner brokers and deploy on their infrastructure. Until then, sell what is fully compliant today: backtesting with real costs, paper trading, alerts, journal, courses, and white-label for brokers (who are themselves looking for compliant no-code algo products, which makes the white-label pitch stronger).
- **Self-use exception:** the owner may run the engine on their own account from their own static IP only if they genuinely qualify as tech-savvy and sign the broker's undertaking.
