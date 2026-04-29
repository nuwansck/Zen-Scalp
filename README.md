# Zen Scalp v1.7.3 — EUR/GBP + AUD/USD M15 Mean Reversion Bot

> **Deployed on Railway · OANDA API · Telegram Alerts**

Automated M15 mean reversion bot trading **EUR/GBP** and **AUD/USD** on OANDA.
Strategy: Bollinger Bands (20, 2σ) + RSI (14). Trades price extremes back toward
the mean. Asian session primary — ranging conditions are ideal for mean reversion.

---

## Strategy

Every 5-minute cycle scores both pairs on **M15** candles:

| Component | Points | Condition |
|---|---|---|
| BB outer band breach (2σ) | +3 | Price beyond Bollinger Band |
| BB approaching (within 10%) | +1 | Price near outer band |
| RSI overbought / oversold | +2 | RSI > 70 or RSI < 30 |
| RSI very extreme (bonus) | +1 | RSI > 80 or RSI < 20 |
| CPR bias | +1 | Price confirms reversion direction |

**SELL** — price ≥ upper BB + RSI overbought → expect reversion down to mean.
**BUY** — price ≤ lower BB + RSI oversold → expect reversion up to mean.

**Per-pair targets (v1.7+):** EUR/GBP and AUD/USD now use different SL/TP
sized to each pair's actual daily range. EUR/GBP keeps wider targets
(daily ATR ~45p), AUD/USD uses tighter targets (more reactive intraday).

| Pair | TP | SL | RR |
|---|---|---|---|
| EUR/GBP | 30p | 20p | 1.5× |
| AUD/USD | 22p | 15p | 1.47× |

Score ≥ 4 → trade. Score 5–6 → full $60. Score 4 → partial $45.

---

## Two-Step Trailing Break-even (v1.7+)

The bot uses a two-stage trailing breakeven. Each stage is per-pair configurable.

**Step 1 — Initial protection.** When unrealized profit reaches
`be_trigger_pips`, SL moves past entry by `be_lock_pips` in the trade's favor.
Trade can no longer become a full loss.

**Step 2 — Profit lock trail (NEW in v1.7).** If MFE continues further to
`be_step2_trigger_pips`, SL moves again to lock `be_step2_lock_pips`.
Captures the "near-TP revert" pattern without capping the runner.

| | EUR/GBP | AUD/USD |
|---|---|---|
| Step 1 trigger | +15p MFE | +11p MFE |
| Step 1 lock | entry +3p | entry +3p |
| Step 2 trigger | **+25p MFE** | **+18p MFE** |
| Step 2 lock | **entry +13p** | **entry +10p** |
| TP | 30p | 22p |

Worst-case after Step 2 fires: locked profit at the Step 2 lock value.
Best case unchanged: TP fires at full target.

Configurable via `be_step2_trigger_pips`, `be_step2_lock_pips` (per-pair
override), `be_step2_enabled` (global kill-switch).

---

## Weekend Gap Protection (v1.6.1+)

Every Friday at **22:00 SGT**, the bot force-closes all open positions at
market — regardless of P&L, regardless of BE protection. This eliminates
weekend gap risk: forex markets close Friday and reopen Sunday, and major
news over the weekend can cause Monday-open prices to gap dozens of pips
past any pre-set SL with significant slippage.

| | Without weekend close | With weekend close |
|---|---|---|
| Best case (no gap) | Trade continues, may reach TP | Trade closes near current price |
| Worst case (gap) | SL fills with major slippage (e.g. −40p) | Trade already closed safely (e.g. +3p) |

Configurable via `weekend_close_enabled`, `weekend_close_hour_sgt`,
`weekend_close_minute_sgt`. Trade close uses OANDA's position-close API
(market order, "ALL" units). A dedicated 🌙 Weekend Close Telegram alert is
sent for each closed trade so you can attribute the close correctly during
data review.

---

## Why Zen Scalp is different from Cable / RF MP

| | Cable / RF MP Scalp | Zen Scalp |
|---|---|---|
| Signal | EMA momentum + ORB | BB + RSI mean reversion |
| Pairs | GBP/USD, EUR/USD, JPY pairs | EUR/GBP + AUD/USD |
| Timeframe | M5 | M15 |
| Primary session | London | Asian |
| Market condition | Trending | Ranging |
| When it wins | Trending weeks | Choppy / ranging weeks |

**Zero overlap** — different pairs, signal, timeframe, session, and market condition.

---

## Sessions (SGT = UTC+8)

```
✈️  04:00–07:59  Dead zone       No entries · BE / SL management active
🗼 08:00–15:59  Asian           score ≥4  cap 6  ← PRIMARY
🇬🇧 16:00–20:59  London          score ≥4  cap 6  ← SECONDARY
🚫 21:00–00:59  US + US cont    DISABLED (trending hours)
🌙 Fri 22:00 SGT  Weekend close   Force-closes ALL open positions (gap protection)
```

Day reset: 08:00 SGT · Loss cap: 6/day · Global cap: 2 open trades (1 per pair).

---

## Risk & Position Sizing

| | EUR/GBP | AUD/USD |
|---|---|---|
| Full position (score 5–6) | $60 | $60 |
| Partial position (score 4) | $45 | $45 |
| pip_value_usd | $11.00 (GBP-quoted) | $10.00 (USD-quoted) |
| TP | 30p | **22p** |
| SL | 20p | **15p** |
| BE Step 1 trigger | +15p | **+11p** |
| BE Step 1 lock | +3p | +3p |
| BE Step 2 trigger | +25p | **+18p** |
| BE Step 2 lock | +13p | **+10p** |
| RR | 1.5× | 1.47× |

---

## Railway Deployment

Required environment variables:

```
OANDA_API_KEY        → required
OANDA_ACCOUNT_ID     → required (use ...006 demo account)
OANDA_DEMO           → true
TELEGRAM_BOT_TOKEN   → required
TELEGRAM_CHAT_ID     → required
```

Railway project → Singapore region → persistent volume mounted at `/data`.
Procfile runs `python scheduler.py` as the long-lived process.

---

## Reports

| Report | Schedule | Contents |
|---|---|---|
| Daily | Mon–Fri 04:00 SGT | Trades closed, WR, net P&L, MTD running totals |
| Weekly | Mon 08:15 SGT | Per-session, per-pair, per-setup breakdown, profit factor |
| Monthly | First Monday 08:00 SGT | Month-vs-month delta, streaks, instant-SL count |
| Trade history export | Mon 08:20 SGT | `trade_history.json` snapshot |

---

## Files

```
bot.py                — main trade-cycle orchestrator
signals.py            — BB+RSI signal engine
oanda_trader.py       — OANDA API wrapper (orders, modify_sl, margin)
telegram_templates.py — message templates
telegram_alert.py     — Telegram send wrapper
reporting.py          — daily/weekly/monthly performance reports
news_filter.py        — Forex Factory news lookahead
calendar_fetcher.py   — calendar caching
config_loader.py      — settings bootstrap & validation
scheduler.py          — APScheduler setup, health endpoint
reconcile_state.py    — startup reconcile + closed-trade catch-up
database.py           — SQLite persistence
state_utils.py        — runtime state JSON helpers
analyze_trades.py     — offline trade analyzer
startup_checks.py     — environment / connectivity checks
test_telegram.py      — Telegram smoke test
```
