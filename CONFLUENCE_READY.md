# Zen Scalp v1.1 — Technical Specification & Operations Wiki

**Bot:** Zen Scalp v1.1  **Pairs:** EUR/GBP + AUD/USD  **Exchange:** OANDA (demo)
**Platform:** Railway (Singapore region)  **Timeframe:** M15  **Cycle:** 5 min

---

## 1. Architecture

```
scheduler.py  (APScheduler — every 5 min)
      |
      ├── run_bot_cycle() × 2 pairs (EUR_GBP, AUD_USD)
      |       ├── _guard_phase()     — 14 ordered pre-trade checks
      |       ├── _signal_phase()    — BB + RSI scoring + position size
      |       └── _execution_phase() — margin check → spread check → place_order
      |
      ├── send_daily_report()   — 04:00 SGT Mon–Fri
      ├── send_weekly_report()  — Monday 08:15 SGT (H1 split included)
      ├── send_weekly_export()  — Monday 08:20 SGT (trade_history.json)
      └── send_monthly_report() — First Monday 08:00 SGT (H1 split included)
```

---

## 2. Signal Engine

**File:** `signals.py` → `SignalEngine.analyze(instrument)`
**Candles:** M15 (60 candles fetched per cycle)

| Component | Bull (BUY) | Bear (SELL) | Max |
|---|---|---|---|
| Price ≤ lower BB (2σ breach) | +3 | — | |
| Price ≥ upper BB (2σ breach) | — | +3 | |
| Price approaching lower band (10%) | +1 | — | |
| Price approaching upper band (10%) | — | +1 | |
| RSI ≤ 30 (oversold) | +2 | — | |
| RSI ≥ 70 (overbought) | — | +2 | |
| RSI ≤ 20 (very oversold, bonus) | +1 | — | |
| RSI ≥ 80 (very overbought, bonus) | — | +1 | |
| CPR bias (below pivot) | +1 | — | |
| CPR bias (above pivot) | — | +1 | |
| **Maximum** | | | **6/6** |

**Direction logic:**
- SELL: price ≥ upper BB + RSI overbought → reversion to middle band
- BUY: price ≤ lower BB + RSI oversold → reversion to middle band

---

## 3. Session Schedule

| Session | Window | Threshold | Cap | Notes |
|---|---|---|---|---|
| Dead zone | 04:00–07:59 | No trading | — | |
| Asian | 08:00–15:59 | ≥ 4/6 | 6 | PRIMARY — ranging market |
| London | 16:00–20:59 | ≥ 4/6 | 6 | Secondary |
| US | 21:00–23:59 | 99 (disabled) | — | Trending hours kill reversion |
| US cont | 00:00–03:59 | 99 (disabled) | — | |

US session disabled — EUR/GBP and AUD/USD can trend strongly during US hours,
which makes mean reversion setups dangerous.

---

## 4. Position Sizing

| Score | Position | Units (AUD/USD) | Per pip |
|---|---|---|---|
| 4 | $45 partial | ~15,000 | $1.50/pip |
| 5–6 | $60 full | ~20,000 | $2.00/pip |

pip_value_usd:
- EUR/GBP: $11.00 static (GBP-quoted pair, ~£8.50 converted to ~$11)
- AUD/USD: $10.00 static (USD-quoted pair)

SL: 20p · TP: 30p (~middle band) · RR: 1.5× · Break-even WR: 40%

---

## 5. Pair Characteristics

**EUR/GBP:**
- Most range-bound major pair — EUR and GBP rarely diverge strongly
- Typical daily range: 40–70 pips
- Typical BB width on M15: 30–50 pips
- Spread: 1–2p during London, 2–3p during Asian

**AUD/USD:**
- Home session = Asian (08:00–15:59 SGT) — best ranging behaviour
- RBA influence during Asian hours
- Typical daily range: 50–80 pips
- Spread: 1–2p during London, 2p during Asian

---

## 6. Why Mean Reversion is Uncorrelated

| | Cable/Fiber | Zen |
|---|---|---|
| Signal type | Momentum | Mean reversion |
| Market condition needed | Trending | Ranging |
| When it wins | Trending weeks | Choppy weeks |
| When it loses | Ranging weeks | Strong trending weeks |

These two bot types are **inversely correlated** in choppy vs trending conditions.
Running both = genuine portfolio diversification.

---

## 7. Version History

| Version | Date | Changes |
|---|---|---|
| **v1.0** | **Apr 2026** | **Initial release. EUR/GBP + AUD/USD. M15 BB+RSI mean reversion. Asian primary, London secondary. US disabled. Based on Cable Scalp v1.5 infrastructure.** |
