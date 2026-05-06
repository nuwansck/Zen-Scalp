# Zen Scalp v2.2 — Settings Reference

This file documents every key in `settings.json`. The actual config is the
source of truth; this doc explains what each key does and what the
production-deployed value is.

---

## Identity

| Key | Value |
|---|---|
| `bot_name` | `"Zen Scalp v2.1"` |
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

`enabled: false` skips the pair entirely each cycle (no signals, no trades,
no management). Use to pause one pair without redeploying code.

---

## SL / TP / Break-Even (per-pair, v1.7+)

| Pair | sl_pips | tp_pips | pip_value_usd | be_trigger_pips | be_lock_pips | be_step2_trigger_pips | be_step2_lock_pips |
|---|---|---|---|---|---|---|---|
| EUR/GBP | 20 | 30 | 17.4 (GBP-quoted, SGD home) | 15 | 3 | 25 | 13 |
| AUD/USD | 15 | 22 | 12.9 (USD-quoted, SGD home) | 11 | 3 | 18 | 10 |

**RR:** EUR/GBP 1.50× · AUD/USD 1.47×

**Two-step trailing breakeven:** when MFE reaches `be_trigger_pips`,
SL moves past entry by `be_lock_pips`. If MFE continues further to
`be_step2_trigger_pips`, SL moves again to lock `be_step2_lock_pips`.
Captures the "near-TP revert" pattern without capping the runner.

Per-pair values override the global keys. Set Step 2 trigger/lock to 0
to disable Step 2 for that pair (falls back to single-step BE).

Sanity guard: Step 2 trigger must be > Step 1 trigger AND Step 2 lock must
be > Step 1 lock. Invalid configs auto-disable Step 2 with a warning.

---

## Signal parameters

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

## Position sizing

| Key | Value |
|---|---|
| `position_full_usd` | `60` — risk per trade at score 5–6 |
| `position_partial_usd` | `45` — risk per trade at score 4 |
| `max_total_open_trades` | `2` (1 per pair) |
| `max_concurrent_trades` (per pair) | `1` |
| `min_trade_units` | `1000` — reject micro-orders after margin guard |

`position_full_usd` and `position_partial_usd` are denominated in the
**account home currency** — on a SGD-denominated demo account this is
SGD; on a USD account this is USD. The `_usd` suffix is historical.

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
| `weekend_close_enabled` | `true` | Force-close all open positions at cutoff |
| `weekend_close_hour_sgt` | `22` | Friday cutoff hour for force-close |
| `weekend_close_minute_sgt` | `0` | Friday cutoff minute for force-close |
| `max_trades_tokyo` | `6` | Max trades per Tokyo window |
| `max_trades_london` | `6` | Max trades per London window |

Setting any session start hour to `99` disables that session entirely.
The bot validates this and shows the disabled state in the startup card.

---

## Risk controls

| Key | Value |
|---|---|
| `max_losing_trades_day` | `6` |
| `max_losing_trades_session` | `3` |
| `loss_streak_cooldown_min` | `30` |
| `max_spread_pips` | `3` (global; per-pair `spread_limits` override) |
| `breakeven_enabled` | `true` |
| `be_trigger_pips` | `15` (global default; pair override active) |
| `be_lock_pips` | `3` (global default; pair override active) |
| `be_step2_enabled` | `true` (global kill-switch for Step 2) |
| `be_step2_trigger_pips` | `25` (global default; pair override active) |
| `be_step2_lock_pips` | `13` (global default; pair override active) |
| `h1_filter_enabled` | `true` |
| `h1_filter_mode` | `"soft"` (penalty only) or `"strict"` (block) |
| `h1_soft_penalty` | `-1` (v2.2+: score penalty when counter-trend in soft mode) |
| `h1_ema_period` | `21` |

---

## News filter

| Key | Value |
|---|---|
| `news_filter_enabled` | `true` |
| `news_block_before_min` | `30` |
| `news_block_after_min` | `30` |
| `news_lookahead_min` | `120` |
| `news_medium_penalty_score` | `-1` |

High-impact news within ±30 minutes blocks new entries. Medium-impact news
applies a -1 score penalty. The news cache refreshes from Forex Factory
every 30 minutes (with a configurable cooldown).

---

## Margin guard

| Key | Value |
|---|---|
| `margin_safety_factor` | `0.6` |
| `margin_retry_safety_factor` | `0.4` |
| `auto_scale_on_margin_reject` | `true` |
| `telegram_show_margin` | `true` |

The margin guard is two-layered. First pass uses `margin_safety_factor`
to keep 40% headroom. If OANDA still rejects on insufficient margin,
the retry uses `margin_retry_safety_factor` for a second attempt at
smaller size. Estimated risk/reward in the trade record are recalculated
after the retry succeeds.

---

## Reports & persistence

| Key | Value |
|---|---|
| `daily_report_hour_sgt` / `daily_report_minute_sgt` | `7` / `50` (Mon–Fri 07:50 SGT) |
| `weekly_report_hour_sgt` / `weekly_report_minute_sgt` | `8` / `0` (Mon 08:00 SGT) |
| `monthly_report_hour_sgt` / `monthly_report_minute_sgt` | `8` / `10` (first Mon 08:10 SGT) |
| `db_retention_days` | `90` |
| `db_cleanup_hour_sgt` | `0:15` |
| `db_vacuum_weekly` | `true` |
| `cycle_minutes` | `3` |

---

## `pip_value_usd` maintenance

`pip_value_usd` for EUR/GBP is a **static approximation** of the GBP→USD
conversion factor:

```
pip_value_usd = pip_size × USD_per_GBP × 100,000
              = 0.0001  × 1.35         × 100,000
              = 13.5
```

Set to **13.5** for GBP/USD ≈ 1.35. Review and update when GBP/USD moves
±0.10 from the last set value. AUD/USD is always exactly 10.0 (USD-quoted,
no drift possible).

A future enhancement (v2.x) could query OANDA's
`quoteHomeConversionFactors` per cycle to eliminate static-config drift
entirely.
