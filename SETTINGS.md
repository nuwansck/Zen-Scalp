# Zen Scalp v1.2 — Settings Reference

---

## Identity

| Key | Value |
|---|---|
| `bot_name` | `"Zen Scalp v1.2"` |
| `demo_mode` | `true` |

---

## Pairs

```json
"pairs": {
  "EUR_GBP": { "enabled": true, "pip_size": 0.0001, "max_spread_pips": 3,
               "spread_limits": {"Tokyo": 3, "London": 3, "US": 4} },
  "AUD_USD": { "enabled": true, "pip_size": 0.0001, "max_spread_pips": 3,
               "spread_limits": {"Tokyo": 3, "London": 3, "US": 4} }
}
```

---

## SL / TP

| Key | EUR/GBP | AUD/USD |
|---|---|---|
| `sl_pips` | 20 | 20 |
| `tp_pips` | 30 | 30 |
| `pip_value_usd` | 11.0 (static) | 10.0 (static) |
| `be_trigger_pips` | 22 | 22 |

RR: 1.5× · Break-even WR: 40%

---

## Signal Parameters

| Key | Default | Notes |
|---|---|---|
| `bb_period` | `20` | Bollinger Band period |
| `bb_std_dev` | `2.0` | Standard deviation multiplier |
| `rsi_period` | `14` | RSI period |
| `rsi_overbought` | `70` | RSI sell threshold |
| `rsi_oversold` | `30` | RSI buy threshold |
| `candle_timeframe` | `"M15"` | M15 candles for BB+RSI |
| `signal_threshold` | `4` | Min score to trade |

---

## Position Sizing

| Key | Value |
|---|---|
| `position_full_usd` | `60` (score 5–6) → $2.00/pip |
| `position_partial_usd` | `45` (score 4) → $1.50/pip |
| `max_total_open_trades` | `2` (1 per pair) |

---

## Sessions

```json
"session_thresholds": { "Tokyo": 4, "London": 4, "US": 99 }
```

| Key | Value |
|---|---|
| `dead_zone_end_hour` | `7` (07:59 SGT) |
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
| `max_trades_london` | `6` |
| `max_spread_pips` | `3` |
| `loss_streak_cooldown_min` | `30` |
| `breakeven_enabled` | `false` |

---

## H1 Filter

| Key | Value |
|---|---|
| `h1_filter_enabled` | `true` |
| `h1_filter_mode` | `"soft"` |
