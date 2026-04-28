# Zen Scalp v1.6 — EUR/GBP + AUD/USD M15 Mean Reversion Bot

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
**TP** = 30 pips. **SL** = 20 pips. **RR = 1.5×**.

Score ≥ 4 → trade. Score 5–6 → full $60. Score 4 → partial $45.

---

## Break-even Protection (v1.5+)

When unrealized profit reaches **+15 pips**, SL automatically moves past entry by
**+3 pips** in the trade's favor (locking ~2 pips net after typical spread).

| Direction | Entry | Trigger | New SL |
|---|---|---|---|
| BUY  | 0.71466 | entry + 15p (0.71616) | entry + 3p (0.71496) |
| SELL | 0.87106 | entry − 15p (0.86956) | entry − 3p (0.87076) |

Trade can no longer become a full loss after BE fires. TP30 still reachable —
upside is uncapped.

Configurable via `be_trigger_pips` (global + per-pair) and `be_lock_pips`.

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
```

Day reset: 08:00 SGT · Loss cap: 6/day · Global cap: 2 open trades (1 per pair).

---

## Risk & Position Sizing

| | EUR/GBP | AUD/USD |
|---|---|---|
| Full position (score 5–6) | $60 | $60 |
| Partial position (score 4) | $45 | $45 |
| pip_value_usd | $11.00 (GBP-quoted) | $10.00 (USD-quoted) |
| SL | 20p | 20p |
| TP | 30p | 30p |
| BE trigger | +15p | +15p |
| BE lock | +3p | +3p |
| RR | 1.5× | 1.5× |

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
