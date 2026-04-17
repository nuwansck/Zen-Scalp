# Zen Scalp — Changelog

---

## v1.0.0 — 2026-04-17

Initial release of **Zen Scalp v1.1** — EUR/GBP + AUD/USD M15 mean reversion bot.
Built from Cable Scalp v1.5 infrastructure. Signal engine completely rewritten.

### Strategy — Bollinger Band + RSI Mean Reversion

Completely different from Cable Scalp / Fiber Scalp (momentum) bots.
Trades extremes back toward the mean — profits in ranging/choppy markets.

**Signal components:**
- Bollinger Bands (20, 2σ) on M15 — identify statistical extremes
- RSI (14) — confirm overbought/oversold condition
- CPR pivot bias — directional confirmation +1

**Scoring (0–6):**
- BB outer band breach: +3
- BB approaching outer band (10%): +1
- RSI overbought (≥70) or oversold (≤30): +2
- RSI very extreme (≥80 / ≤20) bonus: +1
- CPR bias: +1

**TP = middle band (SMA20) ≈ 30 pips**
**SL = 20 pips (just outside outer band)**
**RR = 1.5× · Break-even WR = 40%**

### Pairs — EUR/GBP + AUD/USD

| Pair | Why |
|---|---|
| EUR/GBP | Most range-bound major — oscillates rather than trends |
| AUD/USD | Asian session (home hours) = excellent ranging behaviour |

Zero instrument overlap with Cable Scalp (GBP/USD) and Fiber Scalp (EUR/USD).

### Sessions — Asian primary, US disabled

| Session | Status | Reason |
|---|---|---|
| Asian 08:00–15:59 | ≥4 PRIMARY | Ranging — ideal for mean reversion |
| London 16:00–20:59 | ≥4 secondary | Some reversion opportunities |
| US 21:00–23:59 | DISABLED | Trending hours — mean reversion loses |
| US cont 00:00–03:59 | DISABLED | Same reason |

### Infrastructure

Full parity with Cable Scalp v1.5:
- Health server at __main__ entry point (always 200)
- H1 filter soft mode (labels trades for analysis)
- H1 split in weekly and monthly reports
- Weekly trade_history.json export
- All 14 guards active
- Multi-pair support (EUR_GBP + AUD_USD run in same cycle)

### Key differences from Cable Scalp v1.5

| | Cable Scalp v1.5 | Zen Scalp v1.1 |
|---|---|---|
| Signal | EMA + ORB momentum | BB + RSI mean reversion |
| Pairs | GBP/USD | EUR/GBP + AUD/USD |
| Timeframe | M5 | M15 |
| Primary session | London | Asian |
| US session | Enabled | Disabled |
| max_total_open | 1 | 2 (one per pair) |
| max_losing/day | 8 | 6 (tighter) |

---

## v1.1.0 — 2026-04-17

### Fix 1 — Critical: Candle fetch bug

**Problem:** `SignalEngine.analyze()` called `self.trader.get_candles()` which
doesn't exist in `OandaTrader`. Every cycle logged:
`[EUR_GBP] Candle fetch failed: 'OandaTrader' object has no attribute 'get_candles'`
Bot was running but never scoring a signal — zero trades possible.

**Fix:** Rewrote `signals.py` to use the same `_fetch_candles()` internal HTTP
pattern as Cable Scalp v1.5. `SignalEngine` now owns its own `self.session` and
makes OANDA candle API calls directly (same approach, proven in production).

### Fix 2 — Combined session open card

**Problem:** Two separate Telegram cards fired at each session open:
- `EUR_GBP Tokyo Window Open`
- `AUD_USD Tokyo Window Open`

Cluttered and redundant for a multi-pair bot.

**Fix:** New `msg_session_open_multi()` in `telegram_templates.py`. Single card:
```
🗼 Zen Scalp — Asian Window Open  08:00–15:59 SGT
──────────────────────
EUR/GBP  Today: 0 trade(s)  —  |  cap 6
AUD/USD  Today: 0 trade(s)  —  |  cap 6
Scanning for BB + RSI setups...
```

### Fix 3 — Wrong strategy text in session card

**Problem:** Session card said "Scanning for EMA + ORB setups..." — wrong strategy.

**Fix:** Updated to "Scanning for BB + RSI setups..."

**Files changed:** `signals.py` (full rewrite), `telegram_templates.py`,
`bot.py` (session open block)
