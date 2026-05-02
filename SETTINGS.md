# Zen Scalp v2.0 — Settings Reference

---

## Identity

| Key | Value |
|---|---|
| `bot_name` | `"Zen Scalp v2.0"` |
| `demo_mode` | `true` |

---

## Pairs

```json
"pairs": {
  "EUR_GBP": { "enabled": true, "pip_size": 0.0001,
               "spread_limits": {"Tokyo": 3, "London": 3, "US": 4} },
  "AUD_USD": { "enabled": true, "pip_size": 0.0001,
               "spread_limits": {"Tokyo": 3, "London": 3, "US": 4} }
}
```

---

## SL / TP / Break-Even (v1.7 — per-pair split)

| Pair | sl_pips | tp_pips | pip_value_usd | be_trigger_pips | be_lock_pips | be_step2_trigger_pips | be_step2_lock_pips |
|---|---|---|---|---|---|---|---|
| EUR/GBP | 20 | 30 | 13.5 (GBP-quoted, updated v1.9) | 15 | 3 | 25 | 13 |
| AUD/USD | **15** | **22** | 10.0 (USD-quoted) | **11** | 3 | **18** | **10** |

**RR:** EUR/GBP 1.5× · AUD/USD 1.47×

**Two-step trailing breakeven (v1.7+):** when MFE reaches `be_trigger_pips`,
SL moves past entry by `be_lock_pips`. If MFE continues further to
`be_step2_trigger_pips`, SL moves again to lock `be_step2_lock_pips`.
Captures the "near-TP revert" pattern without capping the runner.

Per-pair values override the global keys. Set Step 2 trigger/lock to 0 in
either global or pair config to disable Step 2 for that pair only (falls
back to single-step BE — same behaviour as v1.5/v1.6).

Sanity guard: Step 2 trigger must be > Step 1 trigger AND Step 2 lock must
be > Step 1 lock. Invalid configs auto-disable Step 2 with a warning.

---

## Signal Parameters

| Key | Value | Notes |
|---|---|---|
| `bb_period` | `20` | Bollinger Band period |
| `bb_std_dev` | `2.0` | Standard deviation multiplier |
| `rsi_period` | `14` | RSI period |
| `rsi_overbought` | `70` | SELL threshold |
| `rsi_oversold` | `30` | BUY threshold |
| `candle_timeframe` | `"M15"` | Strategy timeframe |
| `signal_threshold` | `4` | Min score to trade (0–6) |
| `min_rr_ratio` | `1.4` | Minimum RR enforced before order fires |

---

## Position Sizing

| Key | Value |
|---|---|
| `position_full_usd` | `60` — risk per trade at score 5–6 |
| `position_partial_usd` | `45` — risk per trade at score 4 |
| `max_total_open_trades` | `2` — 1 per pair (EUR/GBP + AUD/USD) |
| `max_concurrent_trades` (per pair) | `1` |
| `min_trade_units` | `1000` — reject micro-orders after margin guard |

---

## Sessions (SGT)

```json
"session_thresholds": { "Tokyo": 4, "London": 4, "US": 99 }
```

| Key | Value | Notes |
|---|---|---|
| `dead_zone_start_hour` | `4` | 04:00 SGT — pre-Tokyo |
| `dead_zone_end_hour` | `7` | 07:59 SGT — end of dead zone |
| `tokyo_session_start_hour` | `8` | |
| `tokyo_session_end_hour` | `15` | |
| `london_session_start_hour` | `16` | |
| `london_session_end_hour` | `20` | |
| `us_session_start_hour` | `99` | **Disabled** (historical 0% WR) |
| `us_session_end_hour` | `99` | Disabled |
| `us_session_early_end_hour` | `99` | US continuation disabled |
| `friday_cutoff_hour_sgt` | `23` | No new entries after Fri 23:00 |
| `weekend_close_enabled` | `true` | v1.6.1 — force-close all open positions at cutoff |
| `weekend_close_hour_sgt` | `22` | v1.6.1 — Friday cutoff hour for force-close |
| `weekend_close_minute_sgt` | `0` | v1.6.1 — Friday cutoff minute for force-close |
| `max_trades_tokyo` | `6` | |
| `max_trades_london` | `6` | |

---

## Risk Controls

| Key | Value |
|---|---|
| `max_losing_trades_day` | `6` |
| `max_losing_trades_session` | `3` |
| `loss_streak_cooldown_min` | `30` |
| `max_spread_pips` | `3` (global; per-pair `spread_limits` override) |
| `breakeven_enabled` | `true` |
| `be_trigger_pips` | `15` (global default; pair override active) |
| `be_lock_pips` | `3` (global default; pair override active) |
| `be_step2_enabled` | `true` (v1.7 — global kill-switch for Step 2) |
| `be_step2_trigger_pips` | `25` (global default; pair override active) |
| `be_step2_lock_pips` | `13` (global default; pair override active) |
| `h1_filter_enabled` | `true` |
| `h1_filter_mode` | `"soft"` (penalty only; not a hard block) |
| `h1_ema_period` | `21` |

---

## News Filter

| Key | Value |
|---|---|
| `news_filter_enabled` | `true` |
| `news_block_before_min` | `30` |
| `news_block_after_min` | `30` |
| `news_lookahead_min` | `120` |
| `news_medium_penalty_score` | `-1` |

---

## Margin Guard

| Key | Value |
|---|---|
| `margin_safety_factor` | `0.6` |
| `margin_retry_safety_factor` | `0.4` |
| `auto_scale_on_margin_reject` | `true` |
| `telegram_show_margin` | `true` |

---

## Reports & Persistence

| Key | Value |
|---|---|
| `daily_report_hour_sgt` / `daily_report_minute_sgt` | `7` / `50` (Mon–Fri 07:50 SGT) |
| `weekly_report_hour_sgt` / `weekly_report_minute_sgt` | `8` / `0` (Mon 08:00 SGT; export follows 08:05) |
| `monthly_report_hour_sgt` / `monthly_report_minute_sgt` | `8` / `10` (first Mon 08:10 SGT) |
| `db_retention_days` | `90` |
| `db_cleanup_hour_sgt` | `0:15` |
| `db_vacuum_weekly` | `true` |
| `cycle_minutes` | `5` |

---

## pip_value_usd maintenance (v1.9+)

`pip_value_usd` for EUR/GBP is a static approximation of the GBP→USD conversion
factor (`pip_value_per_pip × USD_per_GBP / 100,000`). Set to **13.5** for
GBP/USD ≈ 1.35 (May 2026). Review and update when GBP/USD moves ±0.10 from
the last set value. AUD/USD is always exactly 10.0 (USD-quoted — no drift possible).

---

## Cleanup notes (v1.6)

The following keys present in v1.4 / v1.5 have been **removed in v1.6** as
unused dead-config carryovers from earlier strategies (RF MP / Cable Scalp).
None are read anywhere in the v1.6 codebase:

```
exhaustion_atr_mult     orb_fresh_minutes      orb_aging_minutes
orb_formation_minutes   ema_fast_period        ema_slow_period
atr_period              m5_candle_count
```

Also fixed in v1.6: `us_session_early_end_hour` default in `config_loader.py`
was `3` (would silently enable US continuation on a fresh deploy if missing
from `settings.json`); now `99` consistently across all defaults.


## v2.0 TP/SL/BE/RR Reliability Fix

This build keeps the v1.9 strategy settings unchanged but hardens trade management:

- Saves the real OANDA `orderFillTransaction.tradeOpened.tradeID` instead of the fill transaction ID. This is required for break-even SL modification and P&L reconciliation via `/trades/{trade_id}`.
- Keeps TP/SL attached on fill using OANDA `takeProfitOnFill` and `stopLossOnFill`.
- Adds a final execution-side RR guard using `min_rr_ratio` before any order is sent.
- Recalculates actual estimated risk/reward after margin scaling or margin-reject retry.
- Aligns EUR/GBP fallback `pip_value_usd` with settings/docs at `13.5`.

### Current TP/SL/BE/RR settings

| Pair | SL | TP | RR | BE Step 1 | BE Step 2 |
|---|---:|---:|---:|---|---|
| EUR/GBP | 20 pips | 30 pips | 1.50 | +15p trigger, lock +3p | +25p trigger, lock +13p |
| AUD/USD | 15 pips | 22 pips | 1.47 | +11p trigger, lock +3p | +18p trigger, lock +10p |
