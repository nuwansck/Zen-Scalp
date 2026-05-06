# Zen Scalp v2.2 — Technical Specification

**Bot:** Zen Scalp v2.0   **Pairs:** EUR/GBP + AUD/USD   **Exchange:** OANDA (demo)
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

## 3. Two-Step Trailing Break-Even (v1.7+)

`check_breakeven` runs every cycle on each pair's open trades. State lives
on the trade itself: `breakeven_step` (0 = untouched, 1 = step 1 fired, 2 =
step 2 fired). Backward-compat: trades with the legacy `breakeven_moved:
true` flag and no step field are treated as `breakeven_step: 1`.

**Step 1** (BE protection floor). When unrealized MFE reaches
`be_trigger_pips`:

```
BUY  trade:  new_sl = entry + (be_lock_pips × pip_size)
SELL trade:  new_sl = entry − (be_lock_pips × pip_size)
```

Trade can no longer become a full loss after Step 1.

**Step 2** (profit lock trail — NEW v1.7). When MFE further reaches
`be_step2_trigger_pips`:

```
BUY  trade:  new_sl = entry + (be_step2_lock_pips × pip_size)
SELL trade:  new_sl = entry − (be_step2_lock_pips × pip_size)
```

Captures "near-TP revert" outcomes (peak 25-29p that reverses without
hitting TP30) by locking a meaningful profit floor.

**Resolution order:** `pair_sl_tp[PAIR]` override → global setting → hardcoded
default. Records `breakeven_step`, `breakeven_moved`, and `be_locked_pips`
on the trade for audit. Idempotent — never re-fires the same step.

**Sanity guard:** Step 2 trigger must be > Step 1 trigger AND Step 2 lock
must be > Step 1 lock. Invalid configs auto-disable Step 2 with a warning.

**Disable Step 2 globally:** `be_step2_enabled: false`.
**Disable Step 2 per-pair:** set the pair's `be_step2_trigger_pips: 0` or
`be_step2_lock_pips: 0`.

**Per-pair v1.7 values:**

| | Step 1 trigger | Step 1 lock | Step 2 trigger | Step 2 lock |
|---|---|---|---|---|
| EUR/GBP | +15p | entry +3p | +25p | entry +13p |
| AUD/USD | +11p | entry +3p | +18p | entry +10p |

OANDA SL price formatted at instrument's `displayPrecision` (5 for forex,
3 for JPY pairs, 2 for gold) — fixed in v1.5 from a hard-coded `%.2f`.

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

`pip_value`: EUR/GBP = $13.50 (GBP-quoted) · AUD/USD = $10.00 (USD-quoted)

**Per-pair SL/TP/BE (v1.7 split):**

| | EUR/GBP | AUD/USD |
|---|---|---|
| TP | 30p | 22p |
| SL | 20p | 15p |
| BE Step 1 trigger | +15p | +11p |
| BE Step 1 lock | +3p | +3p |
| BE Step 2 trigger | +25p | +18p |
| BE Step 2 lock | +13p | +10p |
| RR | 1.5× | 1.47× |

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
| v1.2 | 2026-04-17 | SL/TP injection fix (18p → 20p). EUR/GBP pip_value 13.5. AUD_USD defaults. |
| v1.3 | 2026-04-17 | Full codebase cleanup. All stale refs removed. Clean docs. |
| v1.4 | 2026-04-17 | `max_trades_tokyo` set to 6 — matches London cap. Tokyo cap fixed on startup card. |
| v1.5 | 2026-04-18 | **BE enabled** with configurable `be_lock_pips`. Fixed `modify_sl` precision bug. |
| v1.6 | 2026-04-28 | Maintenance: weekly report `KeyError` fix. Removed 8 dead config keys (ORB/EMA/ATR carryovers). Fixed M5 → M15 timeframe label in DB. Fixed `us_session_early_end_hour` default 3 → 99. Removed disabled `workflow.yml`. Doc refresh. |
| v1.6.1 | 2026-04-28 | Weekend gap-risk protection. New `force_close_for_weekend()` runs every cycle on Friday from 22:00 SGT, force-closes all open positions via OANDA position-close API. New 🌙 Telegram alert template. |
| v1.7 | 2026-04-29 | **Per-pair parameter split + Two-step trailing breakeven.** EUR/GBP keeps TP30/SL20/BE15+3 unchanged. AUD/USD reduced to TP22/SL15/BE11+3. Both pairs gain Step 2 BE (EUR/GBP +25p→lock+13, AUD/USD +18p→lock+10). New `breakeven_step` field on trades. Session-open Telegram dedup moved per-pair → global. |
| v1.7.1–1.7.3 | 2026-04-29 | Three cleanup passes. Pyflakes 24→0. Removed 4 orphan templates + 1 orphan helper. Stale module docstrings refreshed. CPR→Zen references corrected. |
| v1.8 | 2026-04-30 | **UX milestone.** Combined `msg_trading_window_closed()` card (was 2× per cycle, now 1 per transition). Pretty pair display `EUR/GBP` / `AUD/USD` in Telegram. US session disabled lines split with 🇺🇸 vs 🌙 icons. |
| v1.9 | 2026-05-01 | **Position sizing fix.** EUR/GBP was sized using GBP price-distance instead of USD-adjusted `sl_usd_rec`, causing ~24% over-target risk. `_signal_phase` sizing now uses `sl_usd_rec` for unit calc; `sl_price_dist` retained for order placement. EUR/GBP `pip_value_usd` updated 11.0 → 13.5 (GBP/USD ≈ 1.35). AUD/USD unchanged (USD-quoted, sizing was correct). |
| v2.0 | 2026-05-04 | **TP/SL/BE/RR reliability fix.** Persists real OANDA `orderFillTransaction.tradeOpened.tradeID` (was fill transaction ID — `/trades/{trade_id}` lookups were silently using wrong ID). Final RR execution guard before order send. Recalc `estimated_risk_usd` / `estimated_reward_usd` after margin retry. EUR/GBP fallback `pip_value_usd` aligned to 13.5. |
| **v2.1** | **2026-05-04** | **Polish & docs release.** Unified `min_rr_ratio` fallback default to 1.4 across all sites (was inconsistent: 1.4 in some, 1.6 in others — settings.json value used in production so no behavioural impact). Refreshed README, SETTINGS, CONFLUENCE, CHANGELOG. All 17 user-facing Telegram templates render-tested. No strategy changes. |
| **v2.2** | **2026-05-06** | **Critical hotfix — H1 + sizing.** Two bugs fixed: (1) H1 soft-mode penalty was documented but never coded — soft mode was effectively observe-only. New `h1_soft_penalty` setting (default -1) applies to score when signal is counter-trend. (2) `pip_value_usd` corrected for SGD home account: AUD/USD 10.0→12.9, EUR/GBP 13.5→17.4. Both pairs were ~29% oversized. Counterfactual on May 4-6 data: -$258 → -$65 SGD (75% loss reduction). |

---

## 12. Current production settings (v2.2)

| Setting | EUR/GBP | AUD/USD |
|---|---:|---:|
| SL | 20 pips | 15 pips |
| TP | 30 pips | 22 pips |
| RR | 1.50 | 1.47 |
| BE Step 1 trigger / lock | +15p / +3p | +11p / +3p |
| BE Step 2 trigger / lock | +25p / +13p | +18p / +10p |
| pip_value_usd (SGD per 100k) | 17.4 | 12.9 |
| Full position size | $60 SGD | $60 SGD |
| Partial position size | $45 SGD | $45 SGD |

| Global setting | Value |
|---|---|
| `cycle_minutes` | 3 |
| `signal_threshold` | 4 |
| `min_rr_ratio` | 1.4 |
| `max_total_open_trades` | 2 (1 per pair) |
| `h1_filter_enabled` | true |
| `h1_filter_mode` | soft |
| `h1_soft_penalty` | -1 (v2.2+) |
| `weekend_close_enabled` | true (Fri 22:00 SGT) |
| `be_step2_enabled` | true |
| US sessions | Disabled |
