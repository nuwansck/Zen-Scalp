# Zen Scalp v1.4 — EUR/GBP + AUD/USD M15 Mean Reversion Bot

> **Deployed on Railway · OANDA API · Telegram Alerts**

Automated M15 mean reversion bot trading **EUR/GBP** and **AUD/USD** on OANDA.
Strategy: Bollinger Bands (20, 2σ) + RSI (14). Trades price extremes back toward the mean.
Asian session primary — ranging conditions are ideal for mean reversion.

---

## Strategy

Every 5-minute cycle scores both pairs on M15 candles:

| Component | Points | Condition |
|---|---|---|
| BB outer band breach (2σ) | +3 | Price beyond Bollinger Band |
| BB approaching (within 10%) | +1 | Price near outer band |
| RSI overbought / oversold | +2 | RSI > 70 or RSI < 30 |
| RSI very extreme (bonus) | +1 | RSI > 80 or RSI < 20 |
| CPR bias | +1 | Price confirms reversion direction |

**SELL** — price ≥ upper BB + RSI overbought → expect reversion down to mean
**BUY** — price ≤ lower BB + RSI oversold → expect reversion up to mean
**TP** = middle band (SMA20) ≈ 30 pips. **SL** = 20 pips (just outside band).

Score ≥ 4 → trade. Score 5–6 → full $60. Score 4 → partial $45.

---

## Why Zen Scalp is different from Cable/Fiber

| | Cable / Fiber Scalp | Zen Scalp |
|---|---|---|
| Signal | EMA momentum + ORB | BB + RSI mean reversion |
| Pairs | GBP/USD, EUR/USD | EUR/GBP + AUD/USD |
| Timeframe | M5 | M15 |
| Primary session | London | Asian |
| Market condition | Trending | Ranging |
| When it wins | Trending weeks | Choppy/ranging weeks |

**Zero overlap** — different pairs, signal, timeframe, session, and market condition.

---

## Sessions (SGT = UTC+8)

```
✈️  04:00–07:59  Dead zone       No entries
🗼 08:00–15:59  Asian           score ≥4  cap 6  ← PRIMARY
🇬🇧 16:00–20:59  London          score ≥4  cap 6  ← SECONDARY
🚫 21:00–00:59  US + US cont    DISABLED (trending hours)
```

Day reset: 08:00 SGT · Loss cap: 6/day · Global cap: 2 (1 per pair)

---

## Risk & Position Sizing

| | EUR/GBP | AUD/USD |
|---|---|---|
| Full position | $60 (score 5–6) | $60 (score 5–6) |
| Partial position | $45 (score 4) | $45 (score 4) |
| pip_value_usd | $11.00 (GBP-quoted) | $10.00 (USD-quoted) |
| SL | 20p | 20p |
| TP | 30p | 30p |
| RR | 1.5× | 1.5× |
| Break-even WR | 40% | 40% |

---

## Railway Deployment

```
OANDA_API_KEY        → required
OANDA_ACCOUNT_ID     → required (use ...006 demo account)
OANDA_DEMO           → true
TELEGRAM_BOT_TOKEN   → required
TELEGRAM_CHAT_ID     → required
```

New Railway project → Singapore region → persistent volume at `/data`
