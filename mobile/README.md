# TradeCognix mobile (phase 3 plan)

Expo (React Native, TypeScript) app for clients, plus an admin mode for DevCognix staff. It talks to the same FastAPI backend as `web/` (REST + `WS /ws/live`). Nothing is built yet; this is the plan.

## Stack

- **Expo SDK** (managed workflow), **TypeScript**, **Expo Router** for navigation.
- **TanStack Query** for API calls and caching; **Zustand** for small local state.
- **expo-secure-store** for the JWT (never AsyncStorage). Biometric unlock with **expo-local-authentication**.
- **Charts**: `react-native-wagmi-charts` or `victory-native` (equity curve, candles with entry/exit markers). TradingView Lightweight Charts inside a WebView for the full chart screen.
- **Push**: Expo Notifications (free) for fills, SL hits, daily P&L, kill-switch confirmations.
- Design tokens shared with web: Ink `#0B1020`, Navy `#121A30`, Gold `#C9A86A` / `#E9D3A3`, Ivory `#F6F3EC`; Fraunces (display), Manrope (UI), JetBrains Mono (numbers) via `expo-font`.
- Builds with **EAS Build** (free tier) for Android first, then iOS.

## Screens (client)

1. **Welcome / Sign in / Create account**: email + password, biometric unlock afterwards.
2. **Home / Dashboard**: today's P&L across sessions, running strategies with status chips, market status (09:15-15:30 IST), big red **Kill switch**.
3. **Strategy Studio**: text box (plus voice input, Hinglish supported), example prompts, "Generate", parsed rules as editable chips, plain-English explanation.
4. **Backtest results**: metric cards (trades, win rate, net P&L, max drawdown, profit factor, expectancy), equity curve, cost breakdown, whipsaws, trade list; data-source flags (synthetic / estimated premiums) shown clearly.
5. **Sweep**: timeframe × mode × entry-time grid ranked by net P&L.
6. **My strategies**: list, duplicate, edit, delete; version history.
7. **Paper / Live**: start paper; go-live flow = server-enabled check → strategy confirmation → type LIVE → biometric confirm.
8. **Live monitor**: position, legs, open P&L, events stream from the WebSocket, stop / kill.
9. **Journal**: closed trades by strategy tag, filters, notes, tags, CSV export share sheet.
10. **Brokers**: connect / test / funds for each broker; static-IP status.
11. **Alerts & settings**: push preferences, Telegram link, risk defaults, sign out, delete account.

## Screens (admin mode, role = admin)

1. **Platform stats** (same numbers as `admin/`).
2. **Users**: search, promote/demote, disable.
3. **Live sessions**: running sessions, kill one, **kill all** with typed confirmation.
4. **Strategies**: all strategies, live-confirmed filter.
5. **Incidents**: failed orders, broker errors, halted sessions.

## API mapping

| Screen | Endpoints |
|---|---|
| Auth | `POST /api/auth/login`, `POST /api/auth/register`, `GET /api/auth/me` |
| Studio | `POST /api/strategies/parse`, `POST /api/strategies`, `PUT /api/strategies/{id}` |
| Backtest / sweep | `POST /api/backtests/run`, `POST /api/backtests/sweep`, `GET /api/backtests/{id}/trades` |
| Paper / live | `POST /api/strategies/{id}/confirm-live`, `POST /api/engine/start`, `POST /api/engine/{sid}/stop`, `POST /api/engine/kill`, `WS /ws/live` |
| Journal | `GET /api/journal`, `GET /api/journal/summary`, `PATCH /api/journal/{id}` |
| Brokers | `GET /api/brokers`, `POST /api/brokers/accounts`, `POST /api/brokers/accounts/{id}/test`, `GET /api/brokers/accounts/{id}/funds` |
| Admin | `/api/admin/*` |

## Milestones

1. Auth, Studio, backtest results (read-only) on Android internal testing.
2. Paper trading + live monitor + push notifications.
3. Live go-live flow with biometrics, journal, brokers.
4. Admin mode; iOS TestFlight; Play Store / App Store release.
