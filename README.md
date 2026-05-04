# Zen Scalp v2.1 — EUR/GBP + AUD/USD M15 Mean Reversion Bot

> **Deployed on Railway · OANDA API · Telegram Alerts**

Automated M15 mean-reversion bot trading **EUR/GBP** and **AUD/USD** on
OANDA. Strategy: Bollinger Bands (20, 2σ) + RSI (14) + CPR pivot bias.
Trades price extremes back toward the mean. Asian session is the primary
window — ranging conditions favour mean reversion.

---

## Strategy

Every cycle (3 minutes by default) the bot scores both pairs on **M15**
candles:

| Component | Points | Condition |
|---|---|---|
| BB outer band breach (2σ) | +3 | Price beyond Bollinger Band |
| BB approaching (within 10%) | +1 | Price near outer band |
| RSI overbought / oversold | +2 | RSI > 70 or RSI < 30 |
| RSI very extreme (bonus) | +1 | RSI > 80 or RSI < 20 |
| CPR bias | +1 | Price confirms reversion direction |
| H1 trend penalty (counter-trend) | -1 | Soft mode: subtract 1 if H1 disagrees |
| News-window penalty | -1 | High-impact event within ±30 min |

**SELL** — price ≥ upper BB + RSI overbought → expect reversion down to mean.
**BUY** — price ≤ lower BB + RSI oversold → expect reversion up to mean.

Score ≥ 4 → trade. Score 5–6 → full position ($60). Score 4 → partial
position ($45). All amounts are in **account home currency** — on a
SGD-denominated demo account this is SGD; on a USD account this is USD.

---

## Per-pair targets

EUR/GBP and AUD/USD use different SL/TP sized to each pair's daily range.
EUR/GBP keeps wider targets (daily ATR ~45p), AUD/USD uses tighter targets
(more reactive intraday).

| Pair | SL | TP | RR | Pip value (USD) |
|---|---:|---:|---:|---:|
| EUR/GBP | 20p | 30p | 1.50 | 13.5 |
| AUD/USD | 15p | 22p | 1.47 | 10.0 |

`pip_value_usd` is the per-100k-units USD value of one pip. For USD-quoted
pairs (AUD/USD) this is exactly 10. For non-USD-quoted pairs (EUR/GBP)
it tracks the quote currency's USD rate — currently 13.5 for GBP/USD ≈ 1.35.
Update this value in `pair_sl_tp.EUR_GBP.pip_value_usd` if GBP/USD moves
materially (±0.10).

---

## Two-step trailing break-even

The bot uses a two-stage trailing breakeven, configurable per-pair.

**Step 1 — Initial protection.** When unrealized profit reaches
`be_trigger_pips`, SL moves past entry by `be_lock_pips` in the trade's
favor. Trade can no longer become a full loss.

**Step 2 — Profit lock trail.** If MFE continues further to
`be_step2_trigger_pips`, SL moves again to lock `be_step2_lock_pips`.
Captures the "near-TP revert" pattern without capping the runner.

| | EUR/GBP | AUD/USD |
|---|---|---|
| Step 1 trigger | +15p MFE | +11p MFE |
| Step 1 lock | entry +3p | entry +3p |
| Step 2 trigger | +25p MFE | +18p MFE |
| Step 2 lock | entry +13p | entry +10p |
| TP | 30p | 22p |

Worst case after Step 2 fires: locked profit at the Step 2 lock value.
Best case unchanged: TP fires at full target.

Configurable via `be_step2_trigger_pips`, `be_step2_lock_pips` (per-pair
overrides), `be_step2_enabled` (global kill-switch).

---

## Weekend gap protection

Every Friday at **22:00 SGT**, the bot force-closes all open positions at
market — regardless of P&L, regardless of BE protection. This eliminates
weekend gap risk: forex closes Friday and reopens Sunday, and major news
can cause Monday-open prices to gap dozens of pips past any pre-set SL
with significant slippage.

| | Without weekend close | With weekend close |
|---|---|---|
| Best case (no gap) | Trade continues, may reach TP | Trade closes near current price |
| Worst case (gap) | SL fills with major slippage (e.g. −40p) | Trade already closed safely (e.g. +3p) |

Configurable via `weekend_close_enabled`, `weekend_close_hour_sgt`,
`weekend_close_minute_sgt`. The close uses OANDA's position-close API
(market order, "ALL" units). A dedicated 🌙 Weekend Close Telegram alert
fires for each closed trade so you can attribute the close correctly
during data review.

---

## Sessions (SGT = UTC+8)

```
✈️  04:00–07:59  Dead zone     — No entries, BE/SL management active
🗼 08:00–15:59  Tokyo         — score ≥4, cap 6  ← PRIMARY
🇬🇧 16:00–20:59  London        — score ≥4, cap 6
🇺🇸 21:00–23:59  US session    — DISABLED
🌙 00:00–03:59  US continuation — DISABLED
🌙 Fri 22:00 SGT  Weekend close  — Force-closes all open positions
```

Day reset: 08:00 SGT  ·  Loss cap: 6/day  ·  Global cap: 2 open trades (1 per pair).

---

## Reliability features (v2.0+)

- **Real OANDA trade ID persistence** — the bot saves
  `orderFillTransaction.tradeOpened.tradeID` (not the fill transaction
  ID) so `/trades/{trade_id}` lookups for BE management and P&L
  reconciliation work correctly.
- **TP/SL attached on fill** via OANDA's `takeProfitOnFill` and
  `stopLossOnFill` — protection is in place from the moment the trade
  opens, not on a subsequent modify call.
- **Final RR execution guard** — defense-in-depth check
  (`min_rr_ratio = 1.4`) before order send, in addition to the signal-
  layer check.
- **Margin auto-scale** — if a trade is rejected for insufficient
  margin, the bot retries with a smaller size derived from
  `margin_retry_safety_factor`. Estimated risk/reward in the trade
  record are recalculated to reflect the actual sized risk.

---

## Risk & position sizing

| | EUR/GBP | AUD/USD |
|---|---|---|
| Full position (score 5–6) | $60 | $60 |
| Partial position (score 4) | $45 | $45 |

Sizing formula:

```
units = position_usd / sl_usd_rec
sl_usd_rec = sl_pips × pip_value_usd / 100,000
```

For EUR/GBP this gives correct USD-denominated risk via `pip_value_usd`.
For AUD/USD `sl_usd_rec` numerically equals the price-distance, so sizing
is exact for USD-quoted pairs.

---

## Why Zen Scalp is different from Cable / Fiber Scalp

| | Cable / Fiber Scalp | Zen Scalp |
|---|---|---|
| Signal | EMA momentum + ORB | BB + RSI mean reversion |
| Pairs | GBP/USD, EUR/USD | EUR/GBP + AUD/USD |
| Timeframe | M5 | M15 |
| Primary session | London | Asian |
| Market condition | Trending | Ranging |
| When it wins | Trending weeks | Choppy / ranging weeks |

**Zero overlap** — different pairs, signal, timeframe, session, and
market condition.

---

## Railway deployment

Required environment variables:

```
OANDA_API_KEY        → required
OANDA_ACCOUNT_ID     → required (use ...006 demo account)
OANDA_DEMO           → true
TELEGRAM_BOT_TOKEN   → required
TELEGRAM_CHAT_ID     → required
```

Railway project → Singapore region → persistent volume mounted at
`/data`. Procfile runs `python scheduler.py` as the long-lived process.

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
telegram_templates.py — 19 message templates
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
