# Zen Scalp — Changelog

---

## v1.7.1 — 2026-04-29

**Cleanup pass.** No strategy changes, no functional changes. v1.7.0 deployed
~3 hours earlier, no errors observed. This release reduces dead code surface
area picked up by static analysis (`pyflakes`).

### Removed unused imports

- `bot.py` — removed `import logging` (never referenced), `DATA_DIR` from
  config_loader, and unused telegram template imports: `msg_daily_cap`,
  `msg_new_day_resume`, `msg_session_open` (only `msg_session_open_multi`
  is actually called).
- `signals.py` — removed `from datetime import datetime` (unused).
- `analyze_trades.py` — removed `from pathlib import Path` (unused).
- `calendar_fetcher.py` — removed `date` from datetime imports (unused).
- `reporting.py` — merged duplicate local-scope `from pathlib import Path`
  with the module-level import.

### Removed unused local variables

- `bot.py` `track_max_pips` — `_dp` assigned but never used (was leftover
  from earlier debug logging).
- `oanda_trader.py` `place_order` — `detail` assigned but never used (was
  duplicate of `reason`).
- `calendar_fetcher.py` `_fetch_url` — `usd_events` filtered list never used
  (relevant_events covered the case).
- `reporting.py` `send_daily_report` — `pd_label` formatted but never
  rendered into the message.
- `analyze_trades.py` monthly-PnL block — `sign` variable computed but
  unused (`{:+.2f}` format specifier already adds the sign).

### Verification

- All 16 Python files compile cleanly (`py_compile` no errors).
- `pyflakes` warnings reduced from 24 → 9; remaining 9 are stylistic
  f-strings without placeholders (harmless, no fix needed).
- All JSON files (`settings.json`, `settings.json.example`, `railway.json`)
  validate.
- No changes to `check_breakeven`, `force_close_for_weekend`, signal engine,
  Telegram templates, or any logic path. Pure deletions of dead references.

### What this release does NOT change

- TP/SL/BE values per pair — same as v1.7.0
- Two-step breakeven logic — unchanged
- Session-open dedup — still uses global state file (v1.7 fix preserved)
- Weekend force-close — unchanged
- Any signal calculation, scoring, or trading behavior

Strategy review remains scheduled for ~30-trade milestone (mid-May).

---

## v1.7.0 — 2026-04-29

**First strategy parameter change since v1.5 deploy.** Per-pair split based
on 16 closed trades of live data. Two-step trailing breakeven added to
both pairs. Plus the duplicate session-open Telegram bug fix.

### Why now (16 trades of data)

| | Trades | WR | Net P&L |
|---|---|---|---|
| EUR/GBP | 4 | 100% (4W/0L) | +$189.66 |
| AUD/USD | 12 | 67% (8W/4L) | +$97.97 |

EUR/GBP is genuinely working at TP30 — includes a clean 61-hour TP30 runner
worth +$115. **Don't break what works.**

AUD/USD shows a different pattern. Avg winner closed at **+14.7p**, avg
estimated peak MFE only **+14.1p**. Most trades that reach the BE trigger
at +15p stall there and revert (4 of 7 BE-triggered trades). Strict bimodal
distribution: failure (0p) → BE zone (15-19p) → TP (30+p), with almost
nothing in between.

The asymmetry is now too clean to keep both pairs on identical settings.

### Per-pair split

| | EUR/GBP | AUD/USD (changed) |
|---|---|---|
| TP | 30p | **22p** (was 30) |
| SL | 20p | **15p** (was 20) |
| BE Step 1 trigger | 15p | **11p** (was 15) |
| BE Step 1 lock | +3p | +3p (unchanged) |
| RR | 1.5× | 1.47× ✅ passes 1.4 floor |

AUD/USD changes scale proportionally to the smaller TP. Earlier BE trigger
catches the BE zone earlier with cushion. Tighter SL converts each
"straight stop" failure from −$60 into −$45.

### Two-step trailing breakeven (NEW)

The biggest functional addition. When a trade pushes past Step 1 lock and
keeps running, Step 2 fires at a higher MFE and locks deeper profit.

| | EUR/GBP | AUD/USD |
|---|---|---|
| Step 1 trigger / lock | +15p / +3p | +11p / +3p |
| Step 2 trigger / lock | **+25p / +13p** ← NEW | **+18p / +10p** ← NEW |

**What this protects:** trades that peak in the +25–29p band (or +18–21p
on AUD/USD) and then revert. Under v1.5/v1.6, those trades got knocked back
to BE+3 (~$8 win). Under v1.7, they exit at the Step 2 lock (~$32 on
EUR/GBP, ~$22 on AUD/USD).

**What this doesn't break:** Step 2 fires on the way to TP. If price
continues past Step 2 trigger to hit TP, the trade closes at full TP — no
upside cap. Step 2 only matters if price reverses *between* Step 2 trigger
and TP.

### Code architecture — `check_breakeven` rewrite

State stored on each trade:
```python
trade["breakeven_step"]   # 0 = untouched, 1 = step 1 fired, 2 = step 2 fired
trade["breakeven_moved"]  # legacy bool, kept for backward compatibility
trade["be_locked_pips"]   # audit: which lock value is currently in place
```

Backward compat: trades that existed before v1.7 deploy will have
`breakeven_moved: True/False` but no `breakeven_step` field. The function
infers: `step = 1 if breakeven_moved else 0`. This means existing trade
214 (currently BE-locked from v1.6.1) is correctly read as already at Step
1 and is eligible for Step 2 promotion if its MFE keeps climbing.

Resolution order for all four BE values: `pair_sl_tp[PAIR]` override →
global setting → hardcoded default.

**Sanity guard:** Step 2 trigger must be > Step 1 trigger AND Step 2 lock
must be > Step 1 lock. Invalid configs auto-disable Step 2 with a warning,
falling back to single-step BE behaviour.

**Disable Step 2 globally:** `be_step2_enabled: false`.
**Disable per pair:** set the pair's `be_step2_trigger_pips: 0` (or lock).

### Settings deltas

```diff
  "bot_name": "Zen Scalp v1.7",                       # was "v1.6.1"
+ "be_step2_enabled": true,                           # global kill-switch
+ "be_step2_trigger_pips": 25,                        # global default
+ "be_step2_lock_pips": 13,                           # global default

  "pair_sl_tp": {
    "EUR_GBP": {
      "sl_pips": 20, "tp_pips": 30, "pip_value_usd": 11.0,
      "be_trigger_pips": 15, "be_lock_pips": 3,
+     "be_step2_trigger_pips": 25,
+     "be_step2_lock_pips": 13
    },
    "AUD_USD": {
-     "sl_pips": 20, "tp_pips": 30,
+     "sl_pips": 15, "tp_pips": 22,
      "pip_value_usd": 10.0,
-     "be_trigger_pips": 15, "be_lock_pips": 3
+     "be_trigger_pips": 11, "be_lock_pips": 3,
+     "be_step2_trigger_pips": 18,
+     "be_step2_lock_pips": 10
    }
  }
```

### Bug fix: duplicate session-open Telegram

**Symptom:** every session start (Tokyo at 08:00, London at 16:00), the
🗼/🇬🇧 session-open multi-pair card was being sent **twice** to Telegram.

**Root cause:** `send_once_per_state` was called with `instrument` parameter
which keyed the dedup state to a per-pair `ops_state_<pair>.json` file.
Each pair's cycle independently wrote its own dedup key — so EUR/GBP fired
the card once (writing to `ops_state_eurgbp.json`), then AUD/USD fired the
same card again (writing to `ops_state_audusd.json`). The "send once per
session per day (keyed to first pair only)" comment described intent, not
reality.

**Fix:** new `send_once_global()` helper using a separate
`ops_state_global.json` file shared across all pairs. The session-open
card now dedups on the truly global key.

```python
# Was: per-pair dedup (caused double-send)
send_once_per_state(alert, ops, "session_open_state",
                    f"session_open:{session}:{today}", _card, instrument)

# Now: global dedup (one card per session per day)
send_once_global(alert, "session_open_state",
                 f"session_open:{session}:{today}", _card)
```

### Files changed

```
bot.py                  — check_breakeven 2-step rewrite, send_once_global helper,
                          breakeven_step init on new trades, session-open dedup fix,
                          pair_sl_tp + Step 2 setdefault block
config_loader.py        — pair_sl_tp default updated for v1.7 split
reconcile_state.py      — adds breakeven_step:0 to recovered trade records
telegram_templates.py   — msg_breakeven adds step kwarg, differentiates Step 1/Step 2 headers
settings.json           — Step 2 globals + per-pair Step 2 overrides + AUD/USD reduced TP/SL
version.py              — 1.6.1 → 1.7.0
README.md               — strategy section, BE section, risk table all updated
SETTINGS.md             — SL/TP table extended with Step 2 columns, Risk Controls section
CONFLUENCE_READY.md     — Section 3 (BE) rewritten, Section 6 (Sizing) per-pair, version table
CHANGELOG.md            — this entry
```

### Tests

End-to-end simulation with mocked OANDA trader and alert sender — 5 scenarios:

| Scenario | Result |
|---|---|
| EUR/GBP BUY: walks +9.5 → +15 → +20 → +25 → +28p MFE | Step 1 fires at +15, Step 2 fires at +25, both alerts correct ✓ |
| AUD/USD SELL: walks +5 → +11 → +18p MFE | Step 1 fires at +11, Step 2 fires at +18 ✓ |
| Backward-compat: legacy trade with `breakeven_moved: True` no `breakeven_step` | Correctly inferred as Step 1, advances to Step 2 at +25 ✓ |
| `be_step2_enabled: false` | Step 1 trade ignored at +25 (Step 2 disabled) ✓ |
| Invalid Step 2 config (s2_lock ≤ s1_lock) | Sanity guard fires, Step 2 auto-disabled with warning ✓ |
| Cross-pair session-open dedup | Card sent once per session per day, regardless of which pair's cycle reached the check first ✓ |

All Python files compile clean. JSON valid. SL prices format at correct
displayPrecision (5 for forex).

### What we'll watch at next review (~30 trades)

1. **AUD/USD net P&L:** does the tighter TP+SL improve net? Specifically
   compare loss-side (was -$252 over 4 failures) vs reduced-TP impact
   (would have given up ~$68 of TP30 winners). Break-even point: 5+ failure
   trades vs 3 TP22 winners.

2. **Step 2 hit rate:** how often does Step 2 fire? If it never fires (all
   trades either die before Step 1 or sail to TP), the +25p band is empty
   and Step 2 is dead weight. If it fires frequently and saves real money
   on reverts, it's the right addition.

3. **EUR/GBP unchanged:** WR and net should look like v1.6.1. If it
   regresses, something is wrong with the changes (regression test).

4. **No more duplicate session-open messages.**

### What didn't change

- Signal engine (BB+RSI, M15, score 4 threshold, H1 soft filter)
- Position sizing ($45/$60)
- Session schedule (Tokyo 8-16, London 16-21, US disabled)
- Weekend gap-protection (Fri 22:00 SGT close)
- Margin guard, news filter, dead zone, Friday entry cutoff
- Database, reporting, reconciliation

---

## v1.6.1 — 2026-04-28

**Weekend gap-risk protection.** Force-close all open positions every Friday
from 22:00 SGT to eliminate exposure to weekend gaps when the forex market
reopens Sunday. No strategy parameter changes from v1.6.

### Why

Forex closes Friday late-NY, reopens Sunday late-NY (8pm Mon SGT). Major
news events over the weekend can cause Monday-open prices to gap 30–60 pips
past existing SL levels with significant slippage. A single bad weekend
gap can erase weeks of strategy gains.

Previously, `friday_cutoff_hour_sgt: 23` blocked NEW entries after Friday
23:00 SGT, but did NOT close EXISTING positions. The 61.3-hour EUR/GBP
TP30 win on Apr 17–20 (+$115) survived a weekend by luck — the same setup
in a USD-news weekend could have been a −$200 loss instead.

This release adds a deliberate, configurable safety guard.

### Changes

**`bot.py` — new `is_weekend_close_time()` helper**

Distinct from `is_friday_cutoff` (which only blocks entries). Returns True
when:
- weekday is Friday AND
- current time ≥ `weekend_close_hour_sgt:weekend_close_minute_sgt` AND
- `weekend_close_enabled: true`

**`bot.py` — new `force_close_for_weekend()` management function**

Runs every cycle when `is_weekend_close_time()` is True. For each open
trade on the current pair:
1. Verifies the trade is still open at OANDA (`get_open_trade(trade_id)`)
2. Captures current bid/ask price + pips moved + unrealized P&L for alert
3. Calls `trader.close_position(instrument)` (PUT to OANDA close API)
4. Sends 🌙 weekend-close Telegram alert
5. On failure, logs warning and lets the next cycle retry — idempotent

P&L reconciliation is left to the existing `backfill_pnl` flow on the
following cycle (no special path needed — closed trades just disappear
from `get_open_trade()` results).

**`telegram_templates.py` — new `msg_weekend_close()`**

Distinct visual identity (🌙 emoji) so weekend closes are clearly
attributable in chat history during data review. Shows entry, close
price, pips, P&L, and the Friday cutoff time used.

**`bot.py` cycle integration** — hooked between `check_breakeven` and
`track_max_pips`:
```python
if settings.get("breakeven_enabled", False):
    check_breakeven(history, trader, alert, settings, instrument)

# v1.6.1: weekend gap-risk protection
if is_weekend_close_time(now_sgt, settings):
    force_close_for_weekend(history, trader, alert, settings, instrument, now_sgt)

if track_max_pips(history, trader, settings, instrument):
    save_history(history)
```

Order matters: BE check fires first so any in-flight BE move completes,
then weekend close acts on whatever remains. `track_max_pips` runs last
to record the final MFE before close.

**Settings additions:**

```diff
+ "weekend_close_enabled": true,
+ "weekend_close_hour_sgt": 22,
+ "weekend_close_minute_sgt": 0,
```

Defaults are conservative: 22:00 SGT (1-hour buffer before forex thins
out into Friday close at NY 5pm = SGT 23:00). Disable globally by
setting `weekend_close_enabled: false`.

### What didn't change

- **TP30, SL20, BE+15 lock+3** — same as v1.5/v1.6 (strategy review
  remains scheduled for May 9 at the 30-trade milestone).
- **`friday_cutoff_hour_sgt: 23`** — entry cutoff unchanged. New code
  acts independently.
- **All other parameters** — no strategy tuning in this release.

### Tests

End-to-end smoke test covering all time-boundary cases:

| Case | Result |
|---|---|
| Fri 21:59 — before cutoff | False ✓ |
| Fri 22:00 — exact cutoff | True ✓ |
| Fri 22:30 — after cutoff | True ✓ |
| Fri 23:00 — late Friday | True ✓ |
| Sat 08:00 — already weekend | False ✓ |
| Thu 22:00 — wrong day | False ✓ |
| Mon 22:00 — wrong day | False ✓ |
| Disabled flag respected | True ✓ |
| `is_friday_cutoff` still independent | True ✓ |

Telegram template renders correctly for both BUY/SELL directions and both
positive/negative P&L. All Python files compile clean. JSON valid.

### What to expect Friday May 1, 22:00 SGT

If trades 208 (EUR/GBP) or 214 (AUD/USD) remain open at that time:
1. Cycle fires at 22:00:xx (next 5-min boundary after 22:00)
2. Each open trade closed at market via `close_position()` (ALL units)
3. 🌙 Weekend Close Telegram alert per trade
4. Next cycle at 22:05 reconciles realized P&L through `backfill_pnl`
5. Telegram trade-closed alert fires with final figures

If no trades are open at 22:00 SGT, function exits cleanly with no action.

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
