# Zen Scalp v1.4 — Technical Specification

**Bot:** Zen Scalp v1.4  **Pairs:** EUR/GBP + AUD/USD  **Exchange:** OANDA (demo)
**Platform:** Railway (Singapore)  **Timeframe:** M15  **Cycle:** 5 min

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

---

## 2. Signal Engine (signals.py)

**Candles:** M15 · 60 candles per cycle · both pairs scored independently

| Component | BUY | SELL | Max |
|---|---|---|---|
| Price ≤ lower BB (2σ breach) | +3 | — | |
| Price ≥ upper BB (2σ breach) | — | +3 | |
| Price near lower band (10%) | +1 | — | |
| Price near upper band (10%) | — | +1 | |
| RSI ≤ 30 (oversold) | +2 | — | |
| RSI ≥ 70 (overbought) | — | +2 | |
| RSI ≤ 20 bonus | +1 | — | |
| RSI ≥ 80 bonus | — | +1 | |
| CPR below pivot | +1 | — | |
| CPR above pivot | — | +1 | |
| **Max** | | | **6/6** |

Direction: SELL when overbought at upper band. BUY when oversold at lower band.
TP = middle band (SMA20). SL = just outside outer band.

---

## 3. Session Schedule

| Session | SGT | Threshold | Cap | Notes |
|---|---|---|---|---|
| Dead zone | 04:00–07:59 | — | — | No entries |
| Asian | 08:00–15:59 | ≥ 4/6 | **6** | PRIMARY |
| London | 16:00–20:59 | ≥ 4/6 | 6 | Secondary |
| US | 21:00–23:59 | 99 (disabled) | — | Trending hours |
| US cont | 00:00–03:59 | 99 (disabled) | — | Trending hours |

---

## 4. Position Sizing

| Score | Position | Per pip (EUR/GBP) | Per pip (AUD/USD) |
|---|---|---|---|
| 4 | $45 partial | ~$1.36 | ~$1.50 |
| 5–6 | $60 full | ~$1.82 | ~$2.00 |

pip_value: EUR/GBP = $11.00 (GBP-quoted) · AUD/USD = $10.00 (USD-quoted)
SL: 20p · TP: 30p · RR: 1.5× · Break-even WR: 40%

---

## 5. Global Cap Explained

`max_total_open_trades: 2` — one per pair simultaneously.
`max_concurrent_trades: 1` per pair — never doubles on the same pair.

EUR/GBP open + AUD/USD open = 2 trades = global cap reached.
Not the same as allowing 2 trades on one pair.

---

## 6. Pair Characteristics

**EUR/GBP** — Most range-bound major forex pair. EUR and GBP rarely diverge strongly.
Typical M15 daily range 40–70p. BB touches are reliable. Spread ~1–2p London.

**AUD/USD** — Asian session is AUD's home hours (RBA influence).
Ranges well 08:00–15:59 SGT before London trending begins. Spread ~1–2p.

---

## 7. Version History

| Version | Date | Changes |
|---|---|---|
| v1.0 | Apr 17 2026 | Initial release — EUR/GBP + AUD/USD, BB+RSI signal engine |
| v1.1 | Apr 17 2026 | Fixed healthcheck crash (wrong import). Combined session card. BB+RSI text. |
| v1.2 | Apr 17 2026 | SL/TP injection fix (was 18p → now 20p). EUR/GBP pip_value 11.0. AUD_USD defaults added. Remove (Zen) label. |
| v1.3 | Apr 17 2026 | Full codebase cleanup — all stale refs removed. Clean docs. |
| **v1.4** | **Apr 17 2026** | **max_trades_tokyo set to 6 — matches London cap. Tokyo cap corrected on startup card.** |
