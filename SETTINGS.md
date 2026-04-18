# Zen Scalp v1.4 — Settings Reference

---

## Identity

| Key | Value |
|---|---|
| `bot_name` | `"Zen Scalp v1.4"` |
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

## SL / TP

| Pair | sl_pips | tp_pips | pip_value_usd | be_trigger_pips | be_lock_pips |
|---|---|---|---|---|---|
| EUR/GBP | 20 | 30 | 11.0 (GBP-quoted) | 15 | 3 |
| AUD/USD | 20 | 30 | 10.0 (USD-quoted) | 15 | 3 |

RR: 1.5× · Break-even WR: 40%

Break-even (v1.5): when MFE reaches `be_trigger_pips` (+15), SL is moved past
entry by `be_lock_pips` (+3) in the trade's favor. Locks ~2 pips net after
typical 1p spread. Set `be_lock_pips: 0` for classic "SL to entry" behaviour.

---

## Signal Parameters

| Key | Value | Notes |
|---|---|---|
| `bb_period` | `20` | Bollinger Band period |
| `bb_std_dev` | `2.0` | Standard deviation multiplier |
| `rsi_period` | `14` | RSI period |
| `rsi_overbought` | `70` | SELL threshold |
| `rsi_oversold` | `30` | BUY threshold |
| `candle_timeframe` | `"M15"` | M15 candles |
| `signal_threshold` | `4` | Min score to trade |
| `min_rr_ratio` | `1.4` | Minimum RR enforced |

---

## Position Sizing

| Key | Value |
|---|---|
| `position_full_usd` | `60` — score 5–6 |
| `position_partial_usd` | `45` — score 4 |
| `max_total_open_trades` | `2` — 1 per pair (EUR/GBP + AUD/USD) |
| `max_concurrent_trades` | `1` — per pair |

---

## Sessions

```json
"session_thresholds": { "Tokyo": 4, "London": 4, "US": 99 }
```

| Key | Value |
|---|---|
| `dead_zone_end_hour` | `7` (04:00–07:59) |
| `tokyo_session_start_hour` | `8` |
| `tokyo_session_end_hour` | `15` |
| `london_session_start_hour` | `16` |
| `us_session_start_hour` | `99` (disabled) |
| `us_session_early_end_hour` | `99` (disabled) |

---

## Risk Controls

| Key | Value |
|---|---|
| `max_losing_trades_day` | `6` |
| `max_losing_trades_session` | `3` |
| `max_trades_tokyo` | `6` | Asian (primary) session cap |
| `max_trades_london` | `6` | London (secondary) session cap |
| `max_spread_pips` | `3` |
| `loss_streak_cooldown_min` | `30` |
| `breakeven_enabled` | `true` | v1.5 — was false in v1.4 |
| `be_trigger_pips` | `15` | MFE required to fire BE (global; pair override available) |
| `be_lock_pips` | `3` | v1.5 — pips past entry to lock when BE fires (0 = classic BE) |
| `h1_filter_mode` | `"soft"` |
