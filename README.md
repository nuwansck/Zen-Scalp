# Zen Scalp v1.2 — EUR/GBP + AUD/USD M15 Mean Reversion Bot

> **Deployed on Railway · OANDA API · Telegram Alerts**

Automated M15 mean reversion bot trading **EUR/GBP** and **AUD/USD** on OANDA.
Strategy: Bollinger Bands (20, 2σ) + RSI (14). Trades extremes back toward the mean.
Asian session primary — ranging market is ideal for mean reversion.

---

## Strategy Overview

Every 5-minute cycle the signal engine scores three components on M15 candles:

| Component | Points | Condition |
|---|---|---|
| BB extreme breach | +3 | Price beyond outer Bollinger Band (2σ) |
| BB approaching | +1 | Price within 10% of outer band |
| RSI overbought/oversold | +2 | RSI > 70 or RSI < 30 |
| RSI very extreme | +1 | RSI > 80 or RSI < 20 (bonus) |
| CPR bias | +1 | Price confirms reversion direction vs pivot |

**SELL** when price ≥ upper BB + RSI overbought → expect reversion to mean (middle band)
**BUY** when price ≤ lower BB + RSI oversold → expect reversion to mean (middle band)

Score ≥4/6 → trade. Score 5–6 → full $60. Score 4 → partial $45.

---

## Why Mean Reversion?

Uncorrelated from Cable Scalp (GBP/USD) and Fiber Scalp (EUR/USD) which are
momentum-based. Mean reversion **profits when momentum bots struggle** — choppy,
ranging markets. Zero instrument overlap. Different signal logic entirely.

EUR/GBP is one of the most naturally range-bound major forex pairs — it oscillates
rather than trends. AUD/USD ranges well during Asian session (home currency hours).

---

## Sessions (SGT = UTC+8)

```
✈️  04:00–07:59  Dead zone       No entries
🌏 08:00–15:59  Asian           score ≥4/6  cap 6  ← PRIMARY
🇬🇧 16:00–20:59  London          score ≥4/6  cap 6  ← SECONDARY
🚫 21:00–23:59  US              DISABLED — trending hours kill mean reversion
🚫 00:00–03:59  US cont         DISABLED
```

Day reset: 08:00 SGT. Global cap: 2 open trades (1 per pair).

---

## Risk & Position Sizing

| | Value |
|---|---|
| Full position | $60 (score 5–6) → ~$2.00/pip (AUD_USD) |
| Partial position | $45 (score 4) → ~$1.50/pip (AUD_USD) |
| EUR/GBP SL | 20p |
| EUR/GBP TP | 30p (~middle band) |
| AUD/USD SL | 20p |
| AUD/USD TP | 30p (~middle band) |
| RR | 1.5× |
| Break-even WR | 40% |
| pip_value EUR/GBP | $11.00 (static) |
| pip_value AUD/USD | $10.00 (static) |

---

## Signal Logic

**Bollinger Bands (20, 2σ) on M15:**
Price deviating > 2 standard deviations from the 20-period mean is statistically
extreme — the strategy bets on reversion to that mean.

**RSI (14) confirmation:**
RSI > 70 (overbought) or < 30 (oversold) confirms the extreme and adds confidence.

**TP = Middle Band (SMA20):**
The natural target — price tends to return to the 20-period average.

**SL = Just outside outer band:**
Fixed at 20 pips — if price continues beyond the band, the trade is wrong.

---

## Correlation with existing bots

| | Cable Scalp v1.5 | Fiber Scalp v1.5 | Zen Scalp v1.2 |
|---|---|---|---|
| Pairs | GBP/USD | EUR/USD | EUR/GBP + AUD/USD |
| Signal | EMA + ORB momentum | EMA + ORB momentum | BB + RSI mean reversion |
| Timeframe | M5 | M5 | M15 |
| Primary session | London | London | Asian |
| Market condition | Trending | Trending | Ranging |

**Zero overlap** — different pairs, different signal, different session, different market condition.

---

## Railway Deployment

1. Push folder to GitHub
2. New Railway project → Singapore region
3. Add persistent volume at `/data`
4. Set environment variables (see below)
5. Deploy

### Environment Variables

| Variable | Required |
|---|---|
| `OANDA_API_KEY` | ✅ |
| `OANDA_ACCOUNT_ID` | ✅ |
| `OANDA_DEMO` | ✅ (`true`) |
| `TELEGRAM_BOT_TOKEN` | ✅ |
| `TELEGRAM_CHAT_ID` | ✅ |
