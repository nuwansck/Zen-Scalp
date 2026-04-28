# Zen Scalp v1.6.1 — Technical Specification

**Bot:** Zen Scalp v1.6.1   **Pairs:** EUR/GBP + AUD/USD   **Exchange:** OANDA (demo)
**Platform:** Railway (Singapore)   **Timeframe:** M15   **Cycle:** 5 min

---

## 1. Architecture

```
scheduler.py  (APScheduler — every 5 min)
      ├── run_bot_cycle(EUR_GBP)  ← _guard → _signal → _execute
      ├── run_bot_cycle(AUD_USD)  ← _guard → _signal → _execute
      ├── send_daily_report()     — 04:00 SGT Mon–Fri
      ├── send_weekly_report()    — Mon 08:15 SGT (H1 split)
      ├── send_weekly_export()    — Mon 08:20 SGT (trade_history.json)
      └── send_monthly_report()   — First Mon 08:00 SGT (H1 split)
```

Per-cycle flow inside `run_bot_cycle`:

```
1. guard       — market hours, dead-zone, weekly cutoff, news filter
2. signal      — fetch M15 candles, compute BB/RSI/CPR, score 0–6
3. manage      — track_max_pips (MFE), check_breakeven (BE+lock),
                 force_close_for_weekend (Fri 22:00 SGT — v1.6.1+)
4. execute     — if score ≥ threshold AND no open trade → place order
```

---

## 2. Signal Engine (signals.py)

**Candles:** M15 · 60 candles per cycle · both pairs scored independently.

| Component | BUY | SELL | Max |
|---|---|---|---|
| Price ≤ lower BB (2σ breach) | +3 | — | |
| Price ≥ upper BB (2σ breach) | — | +3 | |
| Price near lower band (≤10%) | +1 | — | |
| Price near upper band (≤10%) | — | +1 | |
| RSI ≤ 30 (oversold) | +2 | — | |
| RSI ≥ 70 (overbought) | — | +2 | |
| RSI ≤ 20 bonus | +1 | — | |
| RSI ≥ 80 bonus | — | +1 | |
| CPR below pivot | +1 | — | |
| CPR above pivot | — | +1 | |
| **Max** | | | **6/6** |

Direction logic: **SELL** when overbought at upper band; **BUY** when oversold
at lower band. TP = +30 pips. SL = −20 pips. RR = 1.5×.

H1 trend filter (`h1_filter_mode: "soft"`) penalises counter-trend signals
without hard-blocking — recorded in trade history for retrospective analysis.

---

## 3. Break-Even Mechanism (v1.5+)

When unrealized profit reaches `be_trigger_pips` (default +15), `check_breakeven`
moves SL past entry by `be_lock_pips` (default +3) in the trade's favor.

```
BUY  trade:  new_sl = entry + (be_lock_pips × pip_size)
SELL trade:  new_sl = entry − (be_lock_pips × pip_size)
```

Resolution order: pair_sl_tp[PAIR] override → global setting → hardcoded default.
Records `breakeven_moved: true` and `be_locked_pips: <n>` on the trade in
`history.json` for audit. Idempotent — never re-fires after activation.

OANDA SL price is formatted using the instrument's `displayPrecision` (5 for
forex majors, 3 for JPY pairs, 2 for gold) — fixed in v1.5 from a hard-coded
`%.2f` that would have rejected forex prices.

---

## 4. Weekend Gap Protection (v1.6.1+)

Every Friday at `weekend_close_hour_sgt:weekend_close_minute_sgt` SGT
(default **22:00**), `force_close_for_weekend()` iterates open positions
on the current pair and closes each via OANDA's position-close API:

```python
PUT /v3/accounts/{account_id}/positions/{instrument}/close
body: {"longUnits": "ALL", "shortUnits": "ALL"}
```

Acts independently of `is_friday_cutoff` (which only blocks new entries).
Idempotent: closed trades disappear from `get_open_trade()` results, so
subsequent cycles skip cleanly. P&L reconciliation flows through the
normal `backfill_pnl` path on the next cycle. A 🌙 weekend-close Telegram
alert fires per closed trade for chat-stream attribution.

Disable by setting `weekend_close_enabled: false` in `settings.json`.

---

## 5. Session Schedule

| Session | SGT | Threshold | Cap | Notes |
|---|---|---|---|---|
| Dead zone | 04:00–07:59 | — | — | No entries; BE/SL management still active |
| Asian (Tokyo) | 08:00–15:59 | ≥ 4/6 | 6 | **PRIMARY** |
| London | 16:00–20:59 | ≥ 4/6 | 6 | Secondary |
| Weekend close | Fri 22:00+ | — | — | Force-close all open positions |
| US | 21:00–23:59 | 99 (disabled) | — | Trending hours |
| US continuation | 00:00–03:59 | 99 (disabled) | — | Trending hours |

Trading day reset: 08:00 SGT. Loss cap: 6/day. Friday entry cutoff: 23:00 SGT.

---

## 6. Position Sizing

| Score | Position USD | Per pip (EUR/GBP) | Per pip (AUD/USD) |
|---|---|---|---|
| 4 | $45 partial | ~$1.36 | ~$1.50 |
| 5–6 | $60 full | ~$1.82 | ~$2.00 |

`pip_value`: EUR/GBP = $11.00 (GBP-quoted) · AUD/USD = $10.00 (USD-quoted)
SL: 20p · TP: 30p · BE trigger: +15p · BE lock: +3p · RR: 1.5×.

Margin guard auto-scales position down when free margin is insufficient
(`auto_scale_on_margin_reject: true`). Falls back to `position_partial_usd`
sizing when `safety_factor` exceeds free margin.

---

## 7. Global Cap Explained

```
max_total_open_trades: 2     ← global ceiling (sum across both pairs)
max_concurrent_trades: 1     ← per pair (never doubles on the same instrument)
```

Allowed: 1 EUR/GBP open + 1 AUD/USD open = global cap reached.
Blocked: 2 trades on the same pair simultaneously.

---

## 8. Pair Characteristics

**EUR/GBP** — Most range-bound major forex pair. EUR and GBP rarely diverge
strongly. Typical daily range 35–55 pips. BB touches are reliable. Spread
~1–2 pips during London. Asian session is thinner — fewer signals.

**AUD/USD** — Asian session is AUD's home hours (RBA influence). Ranges
well 08:00–15:59 SGT before London trending begins. Typical daily range
50–70 pips. Spread ~1–2 pips. Higher volatility than EUR/GBP.

---

## 9. Database & Persistence

```
DATA_DIR=/data           ← persistent volume on Railway
├── trade_history.json   ← FILLED + CLOSED trades, MFE, BE state
├── score_cache.json     ← prevents duplicate alerts within a cycle
├── runtime_state.json   ← last cycle status, debug breadcrumbs
└── zen.sqlite3          ← signals, trade_attempts, runtime, calendar cache
```

`db_retention_days: 90` rolling. Daily VACUUM at 00:15 SGT. Weekly export
of `trade_history.json` to Telegram every Monday 08:20 SGT.

---

## 10. Telegram Reports

| Type | Schedule | Content |
|---|---|---|
| Trade open / close / BE | Real-time | Entry, TP, SL, peak pips, P&L |
| Daily summary | Mon–Fri 04:00 SGT | Trades, WR, net, MTD, best/worst |
| Weekly report | Mon 08:15 SGT | By session / pair / setup, profit factor, streaks |
| Monthly report | First Mon 08:00 SGT | Month-vs-month delta, instant-SL count |
| Trade history export | Mon 08:20 SGT | `trade_history.json` document attached |

---

## 11. Version History

| Version | Date | Changes |
|---|---|---|
| v1.0 | 2026-04-17 | Initial release. EUR/GBP + AUD/USD, BB+RSI signal engine. |
| v1.1 | 2026-04-17 | Fixed healthcheck crash. Combined session card. BB+RSI text. |
| v1.2 | 2026-04-17 | SL/TP injection fix (18p → 20p). EUR/GBP pip_value 11.0. AUD_USD defaults. |
| v1.3 | 2026-04-17 | Full codebase cleanup. All stale refs removed. Clean docs. |
| v1.4 | 2026-04-17 | `max_trades_tokyo` set to 6 — matches London cap. Tokyo cap fixed on startup card. |
| v1.5 | 2026-04-18 | **BE enabled** with configurable `be_lock_pips`. Fixed `modify_sl` precision bug (`:.2f` → `displayPrecision`). |
| v1.6 | 2026-04-28 | Maintenance: weekly report `KeyError` fix. Removed 8 dead config keys (ORB/EMA/ATR carryovers). Fixed M5 → M15 timeframe label in DB. Fixed `us_session_early_end_hour` default 3 → 99. Removed disabled `workflow.yml`. Doc refresh. |
| **v1.6.1** | **2026-04-28** | **Weekend gap-risk protection.** New `force_close_for_weekend()` runs every cycle on Friday from 22:00 SGT, force-closes all open positions via OANDA position-close API. Independent of `friday_cutoff` (which only blocks new entries). Configurable via `weekend_close_enabled/_hour_sgt/_minute_sgt`. New 🌙 Telegram alert template. No strategy changes. |
