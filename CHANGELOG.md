# Zen Scalp — Changelog

---

## v1.6.0 — 2026-04-28

**Maintenance release: bug fix, codebase cleanup, doc refresh.** No strategy
parameter changes — TP30 / SL20 / BE +15 lock +3 unchanged from v1.5.
Strategy review deferred to v1.7 after the 30-trade data milestone (~May 9).

### 🔴 Bug fixes

**`reporting.py` — Weekly report `KeyError: 'wins'`** *(crashed every Monday since v1.0)*

`_session_breakdown()` and `_setup_breakdown()` returned dicts containing
`{count, win_rate, net_pnl}` but `msg_weekly_report._sec()` template tried
to render `s['wins']` and `s['losses']`. Both helpers now include `wins`
and `losses` counts so the template renders cleanly.

Verified by smoke test — full template now produces:
```
By Session
  London     ██████████ 100.0%  1W/0L  $+25.00
  Tokyo      █████░░░░░  50.0%  1W/1L  $+5.00
By Pair
  EUR/GBP    ██████████  50.0%  1W/1L  $+5.00
By Setup
  BB+RSI BUY         ██████████ 100.0%
  BB+RSI SELL        ░░░░░░░░░░   0.0%
```

**`config_loader.py` — `us_session_early_end_hour` default `3` → `99`**

Latent bug: if `settings.json` ever lacked the key on a fresh deploy, the
default would have silently enabled the 00:00–03:59 SGT US continuation
window (historically 0% WR and explicitly disabled for Zen). Now consistent
with `bot.py` (`99`) and matches the live `settings.json`.

**`bot.py` — M5 → M15 timeframe label in database writes**

`db.record_signal()` and `db.record_trade_attempt()` were hardcoding
`timeframe="M5"` while the strategy actually uses M15 candles
(`candle_timeframe: "M15"` in settings, read by `signals.py`). All future
DB rows now correctly tag M15. Existing rows unchanged.

### 🧹 Cleanup

**Removed 8 dead config keys** (set defaults but never read by any v1.6 code).
These were carryovers from the RF MP / Cable Scalp lineage which Zen Scalp
doesn't use:

```
exhaustion_atr_mult     orb_fresh_minutes      orb_aging_minutes
orb_formation_minutes   ema_fast_period        ema_slow_period
atr_period              m5_candle_count
```

Deleted from `bot.py`, `config_loader.py`, and `settings.json`.

**Removed dead ORB code path in `telegram_templates.py`** — the `WATCHING`
signal card had a branch that prepended an "ORB: Nmin (fresh/aging/stale)"
prefix when `orb_formed=True`, but `signals.py` never sets these — the
branch was always dead. Removed the branch and the corresponding
`orb_age_min` / `orb_formed` kwargs from both the template signature and
the caller in `bot.py`.

**Deleted `workflow.yml`** — disabled GitHub Actions workflow with
`if: false` guards. Live deployment is Railway (`python scheduler.py`);
the YAML was inert noise. Removing prevents accidental re-activation.

**Stale version banners** updated across 7 files: `bot.py`, `signals.py`,
`telegram_alert.py`, `telegram_templates.py`, `test_telegram.py`,
`scheduler.py`, `config_loader.py`. All now read "Zen Scalp v1.6".

**Stale comment cleanups** in `bot.py` setdefault block (e.g. removed
`# v1.0: US 21-23 disabled (0% WR)` historical commit-style notes; replaced
with concise current state).

### 📚 Documentation

- **`README.md`** rewritten — adds BE+3 mechanism explanation, expanded file
  layout, reports table, post-Cable/RF strategy contrast updated.
- **`SETTINGS.md`** rewritten — separates SL/TP, signal, sizing, sessions,
  risk controls, news, margin, reports into clear sections. Adds v1.6
  cleanup notes section listing the 8 removed keys.
- **`CONFLUENCE_READY.md`** rewritten — adds Section 3 (Break-Even Mechanism),
  Section 8 (Database & Persistence), Section 9 (Telegram Reports). Version
  history table extended through v1.6.

### Settings deltas

```diff
  "bot_name": "Zen Scalp v1.6",         # was "v1.5"
- "exhaustion_atr_mult": 3.0,
- "orb_fresh_minutes": 60,
- "orb_aging_minutes": 120,
- "ema_fast_period": 9,
- "ema_slow_period": 21,
- "orb_formation_minutes": 15,
- "atr_period": 14,
- "m5_candle_count": 40,
```

### What didn't change (deliberately)

| Setting | Value | Why |
|---|---|---|
| `tp_pips` | 30 | Strategy review deferred to v1.7 after May 9 |
| `sl_pips` | 20 | Same |
| `be_trigger_pips` | 15 | Validated by 4 BE-saved trades on this sample |
| `be_lock_pips` | 3 | ~2 pips net after spread, working as designed |
| `signal_threshold` | 4 | Score 4 outperformed Score 6 on small sample — don't optimize |
| `h1_filter_mode` | "soft" | Counter-trend trades winning at 80% — leave alone |

### Live data context (12 trades closed since v1.5 deploy)

- **Net P&L:** +$81.93 over 12 trades (8W / 4L)
- **EUR/GBP:** 3 trades, 100% WR, net +$178.13
- **AUD/USD:** 9 trades, 56% WR, net −$96.21
- **BE+3 saves:** 4 trades — would have been ~−$235 in losses without v1.5
- **Open positions:** EUR/GBP BUY @ 0.86602, AUD/USD SELL @ 0.71817

Sample still too small (target: 30 trades for confident parameter tuning).

---

## v1.5.0 — 2026-04-18

**Break-even activation with configurable profit-lock.**

### Why
v1.4 ran with `breakeven_enabled: false`. First live-demo data (AUD/USD + EUR/GBP
on Apr 18) showed both trades peaking in the +8–16 pip zone before approaching
TP30. BB reversion setups statistically stall around the middle band (~15–20 pips
from a BB extreme entry), so a 30-pip TP leaves winners exposed to full reversal
if they hit the target zone without follow-through. BE protects this exact
failure mode without capping the occasional runner that reaches TP30.

### Changes

**`oanda_trader.py` — 🔴 Bug fix: `modify_sl` price precision**
- **Before:** SL price formatted with hard-coded `f"{price:.2f}"`, truncating
  forex prices like `0.72070` to `"0.72"`. OANDA rejects these as invalid,
  so every breakeven attempt would have silently failed with a `log.warning`.
- **After:** Looks up `displayPrecision` from the instrument spec cache
  (5 for EUR_GBP / AUD_USD, 2 for gold, 3 for JPY). Accepts optional
  `instrument` arg to skip the lookup round-trip. Falls back to `get_open_trade`
  when instrument not supplied. Matches the precision handling already used
  in `place_order`.
- Dormant in v1.4 because BE was disabled — would have fired on first BE
  activation. Caught during pre-flight review.

**`bot.py` — `check_breakeven()` rewrite**
- New setting: `be_lock_pips` (pair-override aware, same resolution order as
  `be_trigger_pips`: pair → global → default 0).
- Instead of moving SL to exactly the entry price (net ~0 before spread,
  net slightly negative after), move it past entry by `be_lock_pips` in the
  trade's favor:
  - `BUY:  new_sl = entry + lock_dist`   (SL above entry = locked long profit)
  - `SELL: new_sl = entry - lock_dist`   (SL below entry = locked short profit)
- Passes `instrument=` through to `modify_sl` so the price is formatted at
  the correct precision.
- Records `be_locked_pips` on the trade for later analysis.
- Log line now prints the actual new SL price and lock amount for audit.

**`telegram_templates.py` — `msg_breakeven()`**
- When `lock_pips > 0`: shows new SL price and "+Xp locked" badge.
- When `lock_pips == 0`: preserves the classic "SL moved to entry" wording.
- Backward-compatible signature (new kwargs default to `None`/`0`).

**`settings.json` — activation**
```diff
- "breakeven_enabled": false,
+ "breakeven_enabled": true,
+ "be_trigger_pips": 15,
+ "be_lock_pips": 3,
```
Per-pair (`pair_sl_tp`) block also updated: `be_trigger_pips 22 → 15`,
`be_lock_pips: 3` added for both EUR_GBP and AUD_USD. Stale duplicate
`be_trigger_pips: 20` key further down the file was removed.

### Behaviour matrix (AUD/USD SELL @ 0.72070, SL @ 0.72270, TP @ 0.71770)

| Price path                | v1.4 outcome      | v1.5 outcome                  |
|---------------------------|-------------------|-------------------------------|
| Straight to TP            | +30p / +$60       | +30p / +$60 (unchanged)       |
| +17 pips, reverse to SL   | −20p / −$40       | +3p locked / +$6 (saved $46)  |
| +5 pips, reverse to SL    | −20p / −$40       | −20p / −$40 (BE never fired)  |
| Pulls back to BE then TP  | +30p / +$60       | +3p locked / +$6 (trade-off)  |

The bottom row is the only v1.5 regression. Apr 28 review will quantify how
often this happens vs. rows 2 and 3 to confirm net-positive expectancy.

### Data collection for Apr 28 review

Already wired in v1.4 and unchanged in v1.5:
- `max_pips_reached` per trade (MFE) — tracked every cycle in `track_max_pips`
- Shown in `msg_trade_closed` as `Peak: +X.X pips reached`

New in v1.5:
- `breakeven_moved: True/False` on each trade in history
- `be_locked_pips` value recorded for audit

Review metrics:
1. BE trigger hit rate (% of filled trades that reached +15 MFE)
2. Post-BE split: reached TP30 vs. pulled back to BE+3 vs. still open
3. MFE distribution of BE-out trades — tight cluster near +15–17 confirms lock
   is right-sized; scattered to +25+ suggests trigger should move later
4. Counterfactual: for each BE-out, would the original −20 SL have hit?

---

## v1.0.0 — 2026-04-17

Initial release of **Zen Scalp v1.4** — EUR/GBP + AUD/USD M15 mean reversion bot.
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

| | Cable Scalp v1.5 | Zen Scalp v1.4 |
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

---

## v1.2.0 — 2026-04-17

### Fix 1 — SL/TP using emergency fallback (18p) instead of settings (20p)

**Problem:** `signals.py` returned CPR levels dict without injecting
`sl_price_dist` / `tp_price_dist`. `bot.py compute_sl_usd` couldn't find them
→ fell back to hardcoded 18p emergency SL. Observed in first live trade:
`compute_sl_usd: no valid SL in levels — using 18p emergency fallback`

**Fix:** Added full SL/TP/RR/H1 injection block at end of `analyze()`:
- `sl_price_dist` = 20p × pip_size → correct price distance for order placement
- `tp_price_dist` = 30p × pip_size → TP at ~middle band
- `sl_usd_rec`, `tp_usd_rec`, `rr_ratio`, `h1_trend`, `h1_aligned` all injected
- Same pattern as Cable Scalp v1.5 signals.py

### Fix 2 — Wrong pip_value and sl_pips in bot.py defaults

**Problem:** bot.py setdefault had `EUR_GBP sl_pips: 18, pip_value: 10.0`
(copied from Cable Scalp). AUD_USD was missing entirely.

**Fix:**
- EUR_GBP: sl_pips 18 → **20**, pip_value 10.0 → **11.0** (GBP-quoted)
- AUD_USD: **added** sl_pips 20, tp_pips 30, pip_value 10.0
- settings.json updated to match

### Fix 3 — "(Zen)" suffix removed from pair display

**Problem:** Startup card showed `EUR/GBP + AUD/USD (Zen)` — user found
the "(Zen)" suffix redundant given the bot name already says Zen Scalp.

**Fix:** Pair line now shows `EUR/GBP + AUD/USD`

---

## v1.4.0 — 2026-04-17

### Fix — max_trades_tokyo corrected to 6

**Problem:** `max_trades_tokyo` was not set in settings.json, so it fell back to
the default of 10. Startup card showed `Tokyo cap 10` while London showed `cap 6`.
Inconsistent — Asian is the PRIMARY session and should have the same cap.

**Fix:** `max_trades_tokyo: 6` added to settings.json, bot.py defaults,
and SETTINGS.md documentation.

Startup card now shows:
```
🗼 08:00–15:59  Tokyo      cap 6  score≥4
🇬🇧 16:00–20:59  London     cap 6  score≥4
```
