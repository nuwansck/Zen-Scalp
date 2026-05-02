# Zen Scalp — Changelog

---

## v1.9.0 — 2026-05-01

**Critical sizing bug fix: EUR/GBP position size now correctly targets USD risk.**

### Why

Every EUR/GBP trade since launch has been sized with ~24% more risk than the
configured `position_full_usd` / `position_partial_usd` targets. At GBP/USD ≈ 1.36
(current rate), a $45 partial position was placing 22,500 units instead of the
correct ~16,667 units, resulting in actual USD risk of ≈ $55.80 — nearly $11 over
target per trade.

**Root cause:** `compute_sl_usd()` returns a raw price distance
(`sl_pips × pip_size` = e.g. `20 × 0.0001 = 0.0020`). For AUD/USD (USD-quoted)
this is numerically equal to the USD cost per unit, so sizing was accidentally
correct. For EUR/GBP (GBP-quoted), `0.0020` is a GBP distance — not USD. Dividing
`position_usd / sl_price_dist` mixed units (USD ÷ GBP-equivalent), giving a unit
count calibrated to GBP/USD ≈ 1.00, not the actual ~1.36.

The `pip_value_usd` field in `pair_sl_tp` already computed the correct USD value
(`sl_usd_rec = sl_pips × pip_value_usd / 100,000`) but `bot.py` was reading
`sl_price_dist` (primary) instead of `sl_usd_rec` for position sizing.

Identified via post-trade P&L back-calculation:
- Trade 289 (EUR/GBP BUY, score 4 / $45 partial): 22,500 units → actual risk ≈ $55.76
- Target: 16,667 units → actual risk ≈ $45.00

### Changes

**`bot.py` — `_signal_phase()` sizing block (critical fix)**

Split `sl_usd` into two distinct variables with separate roles:

| Variable | Value | Used for |
|---|---|---|
| `sl_price_dist` | `sl_pips × pip_size` (e.g. 0.0020) | OANDA order price levels, pip counts |
| `sl_usd` | `sl_usd_rec` from signals (e.g. 0.0027 at pip_value=13.5) | Position sizing, RR display, reward calc |

```python
# Before (bug — GBP distance used as USD for EUR/GBP sizing):
sl_usd = compute_sl_usd(levels, settings)          # 0.0020 (GBP)
units  = calculate_units_from_position(pos, sl_usd)  # 45/0.0020 = 22,500 ❌

# After (fix — USD-adjusted value used for sizing):
sl_price_dist = compute_sl_usd(levels, settings)     # 0.0020 (GBP, for order)
sl_usd = float(levels.get("sl_usd_rec") or sl_price_dist)  # 0.0027 (USD)
units  = calculate_units_from_position(pos, sl_usd)  # 45/0.0027 = 16,667 ✓
```

Also fixed: `compute_sl_tp_pips()`, `compute_sl_tp_prices()`, and post-fill
`sl_price` / `tp_price` recalculation now correctly use `sl_price_dist` /
`tp_price_dist` (price terms), while `reward_usd`, `rr_ratio`, and the Telegram
trade alert use `sl_usd` / `tp_usd` (true USD values).

**`settings.json` + `settings.json.example` — `pip_value_usd` for EUR/GBP updated**

```diff
  "EUR_GBP": {
    "sl_pips": 20,
    "tp_pips": 30,
-   "pip_value_usd": 11.0,   // GBP/USD ≈ 1.10 — original assumption (stale)
+   "pip_value_usd": 13.5,   // GBP/USD ≈ 1.35 — reflects current rate
```

At pip_value_usd = 13.5:
- `sl_usd_rec` (20p) = 20 × 13.5 / 100,000 = **0.0027** (vs 0.0020 before)
- Full position ($60) → ~22,222 units → actual risk ≈ $60.00 ✓
- Partial position ($45) → ~16,667 units → actual risk ≈ $45.00 ✓

`pip_value_usd` for AUD/USD remains 10.0 — always exact for USD-quoted pairs.

**`signals.py` — added explanatory comments to SL/TP injection block**

Clarifies the distinction between `sl_price_dist` (price-domain, used for orders)
and `sl_usd_rec` (USD-domain, used for sizing). No logic changes.

**`bot.py` — `calculate_units_from_position()` docstring updated**

Old docstring said "Exact USD risk for EUR/GBP + AUD/USD" which was incorrect
before this fix (it was only exact for AUD/USD).

### Impact

| | Before v1.9 | After v1.9 |
|---|---|---|
| EUR/GBP full pos ($60) | ~30,000 units / ~$81.78 actual | ~22,222 units / ~$60.01 actual |
| EUR/GBP partial ($45) | ~22,500 units / ~$55.80 actual | ~16,667 units / ~$45.01 actual |
| AUD/USD full pos ($60) | 40,000 units / $60.00 exact | 40,000 units / $60.00 exact ✓ |
| AUD/USD partial ($45) | 30,000 units / $45.00 exact | 30,000 units / $45.00 exact ✓ |

SL/TP pip values unchanged. Strategy unchanged. No settings removed.

### `pip_value_usd` maintenance note

`pip_value_usd` for EUR/GBP is a **static approximation** of the GBP→USD conversion
factor. It needs periodic review when GBP/USD moves significantly (±0.10). The value
is set to 13.5 for GBP/USD ≈ 1.35 as of May 2026. When GBP/USD moves to a new
sustained level, update this value in `pair_sl_tp.EUR_GBP.pip_value_usd`.

A future enhancement (v2.x) could fetch live GBP/USD from OANDA and compute
`pip_value_usd` dynamically each cycle, eliminating the drift risk entirely.

### Files changed

```
bot.py             — _signal_phase sizing block rewritten; docstring updated
signals.py         — explanatory comments added to SL/TP injection block
settings.json      — pip_value_usd EUR_GBP: 11.0 → 13.5; bot_name → v1.9
settings.json.example — same
version.py         — 1.8.0 → 1.9.0
CHANGELOG.md, README.md, SETTINGS.md, CONFLUENCE_READY.md — version bumps
```

### Verification

- All 16 Python files compile cleanly.
- `pyflakes *.py` → 0 warnings.
- All JSON files valid.
- `sl_usd_rec` populated by `signals.py` for every signal above threshold.
- Emergency fallback (`sl_price_dist` when `sl_usd_rec` absent) preserves
  existing behaviour for any edge case where signals bypass the injection block.
- AUD/USD sizing unchanged (sl_usd_rec == sl_price_dist numerically for USD-quoted).

---

## v1.8.0 — 2026-04-30

**UX milestone release.** Three Telegram polish items, no strategy changes.
First version where the chat experience matches the code quality. Marks the
end of the post-v1.7 cleanup arc — next releases should be data-driven,
not hygiene-driven.

### What changed

#### 1. Combined "Trading Window Closed" card

**Before (v1.7.x):** when London or Tokyo session ended, both pair cycles
independently sent `⏸️ [EUR_GBP] Outside session.` and `⏸️ [AUD_USD]
Outside session.` — two messages per transition. The dedup was per-pair,
so each pair fired its own card.

**Now (v1.8):** single combined card per transition, sent globally:

```
🌙 Trading Window Closed
──────────────────────
🇬🇧 London Window session closed
EUR/GBP and AUD/USD scanning paused
──────────────────────
Next: 🗼 Tokyo  08:00 next day SGT
```

**Implementation:** new `msg_trading_window_closed()` template; replaces
the per-pair `send_once_per_state` call with `send_once_global` (same
pattern v1.7 used for the session-open card duplicate fix). Card fires
exactly once per session-end-per-day, then the bot stays silent until
the next session opens.

Result: ~336 outside-session notifications per night → ~1 per night.

#### 2. Pretty pair names in user-facing strings

User-facing Telegram alerts now display pairs as **`EUR/GBP`** and
**`AUD/USD`** (slash format) instead of the OANDA-native `EUR_GBP` /
`AUD_USD` underscore format. Applied to ALL Telegram templates, including
error templates (`msg_order_failed`, `msg_margin_adjustment`).

**Internal log lines and database rows still use the OANDA underscore
format** — those are programmatic identifiers, not user-facing display.
Reports stay machine-parseable. Clean separation:

| Surface | Format |
|---|---|
| Telegram messages | `EUR/GBP` slash |
| Log lines | `EUR_GBP` underscore (matches OANDA API) |
| Database rows | `EUR_GBP` underscore (queryable) |
| Trade history JSON | `EUR_GBP` underscore (machine-parseable) |

**Implementation:** new `_pretty_pair(instrument)` helper in bot.py.
Applied at 12 user-facing call sites in bot.py. Templates that receive
raw `instrument` strings (`msg_order_failed`, `msg_margin_adjustment`,
`msg_weekend_close`, `msg_session_open_multi`) format internally with
`.replace("_", "/")`. Log lines (4 sites in bot.py) deliberately left
as-is.

#### 3. US session label clarity

Startup card sessions block updated. Old version had two ambiguous lines
that looked nearly identical:

```
🚫 US session   disabled
🚫 US cont.    disabled
```

New version uses distinct icons and always shows hours, even when disabled:

```
🇺🇸 21:00–23:59  US (disabled)
🌙 00:00–03:59  US-cont (disabled)
```

When/if either window is enabled in the future, the icons remain (icons
indicate the session, hours show the window).

### Files changed

```
bot.py                  — _pretty_pair helper added; 12 user-facing sites
                          updated; outside-session block rewritten to
                          use send_once_global + msg_trading_window_closed
telegram_templates.py   — new msg_trading_window_closed template;
                          msg_startup US-session lines updated
settings.json           — bot_name → "Zen Scalp v1.8"
version.py              — 1.7.3 → 1.8.0
README.md, SETTINGS.md, CONFLUENCE_READY.md, CHANGELOG.md — version bumps
                          and this entry
```

### Verification

- All 16 Python files compile cleanly.
- `pyflakes *.py` returns 0 warnings (preserves v1.7.3's clean state).
- All JSON files valid.
- `_pretty_pair("EUR_GBP") == "EUR/GBP"` ✓
- `_pretty_pair("AUD_USD") == "AUD/USD"` ✓
- Startup card renders correctly with new US session lines.
- Trading-window-closed template renders correctly for both
  London → Tokyo and Tokyo → London transitions.

### What this release does NOT change

- TP/SL/BE values per pair — same as v1.7
- Two-step breakeven logic — unchanged
- Weekend close — unchanged (still 🌙 emoji; semantically distinct from
  the new 🌙 trading-window-closed since they fire in different contexts
  and contain different message content)
- Strategy / scoring / execution / signal engine — unchanged
- Reports, reconciliation, telemetry — unchanged

### What to expect after deploy

Tonight at session-end (London closes 20:59 SGT), instead of two
`⏸️ [EUR_GBP] Outside session.` / `⏸️ [AUD_USD] Outside session.`
messages flooding the chat every 5 minutes for the next 11 hours, you'll
see **one** clean "Trading Window Closed" card around 21:00 SGT, then
silence until Tokyo opens at 08:00 SGT next morning.

### Strategy review note

This release closes the v1.6→v1.8 architectural arc:

| Version | Focus | Status |
|---|---|---|
| v1.6   | Maintenance | ✅ Done |
| v1.6.1 | Weekend protection | ✅ Done |
| v1.7   | Strategy: per-pair split + 2-step BE | ✅ Done, monitoring |
| v1.7.x | Three cleanup passes, pyflakes 0 | ✅ Done |
| **v1.8** | **UX polish, milestone release** | **✅ Done** |

Next release should be **strategy-driven** (data review at ~30 trades,
parameter tuning if needed, or v2.0 refactor).

---

## v1.7.3 — 2026-04-29

**Polish pass.** Pyflakes is now 100% clean (was 9 stylistic warnings).
Several stale docstrings and one stale HTTP header fixed. Zero functional
changes — every code path that runs is identical to v1.7.2.

### Pyflakes warnings: 9 → 0

- `analyze_trades.py` (6 sites) — removed `f` prefix from static strings
  with no placeholders. Pure cosmetic, identical at runtime.
- `telegram_templates.py` (2 sites) — same fix in the disabled-US session
  branches inside `msg_startup`.
- `scheduler.py` — the bare `exc` in the metrics endpoint catch-all was
  swallowing exceptions silently. Now logs them at warning level
  (`logger.warning("metrics endpoint error: %s", exc)`) so any future
  HTTP issues are visible without changing behavior.

### Stale docstrings updated

- **`bot.py` module header** — claimed "Single pair, clean data" (we run
  two pairs) and listed an incorrect session schedule ("London 16-20,
  US cont 00-03, Tokyo 08-15 ≥5/6"). Now correctly documents Tokyo
  PRIMARY ≥4 / London SECONDARY ≥4 / US disabled, and the example
  per-pair file paths use `eurgbp`/`audusd` instead of legacy `gbpusd`.
  Also adds a brief mention of the v1.7+ two-step BE and weekend-close
  features in the architecture summary.

- **`reporting.py` module header** — claimed "Daily — Mon–Fri at 15:30
  SGT, 30 min before London open." Actual daily report time is
  **04:00 SGT** (dead-zone start). Updated and added a note that the
  schedule values are configurable via settings.

- **`database.py` module header** — claimed "for the re-architected CPR
  bot." This is Zen Scalp, not CPR Gold. Updated to reference Zen Scalp
  and clarified the database stores cycle-level signals, trade attempts,
  and runtime state.

- **`calendar_fetcher.py` module header** — was a leftover commit-style
  changelog ("Architecture-only improvements:" etc.) that's been stale
  for months. Rewritten as a forward-looking description of what the
  module does now.

### Stale strings fixed

- **`analyze_trades.py` print_report** — header line read
  `📊  CPR GOLD BOT — PERFORMANCE REPORT`. The script reads Zen Scalp's
  trade history; rebrand the header to `📊  ZEN SCALP — PERFORMANCE REPORT`.

- **`calendar_fetcher.py` HTTP request** — User-Agent header was still
  `CableScalp/1.0` from the strategy's lineage. Updated to `ZenScalp/1.7`
  so any FF-side analytics/rate-limiting attributes our traffic correctly.
  Cosmetic but worth fixing while we're in the area.

### Verification

- All 16 Python files compile cleanly.
- `pyflakes *.py` returns exit code 0 with zero output (down from 9).
- All JSON files (`settings.json`, `settings.json.example`, `railway.json`)
  validate.
- All public symbols (`check_breakeven`, `force_close_for_weekend`,
  `send_once_global`, all used Telegram templates) intact.
- Strategy parameters per pair unchanged — see v1.7.0 for those values.
- No imports added or removed, no function signatures changed.

### What this release does NOT change

- TP/SL/BE values per pair — same as v1.7.0/v1.7.1/v1.7.2
- Two-step breakeven logic — unchanged
- Weekend close — unchanged
- Strategy / scoring / execution — unchanged
- Reports, reconciliation, telemetry — unchanged

Strategy review remains scheduled for ~30-trade milestone (mid-May).

This is the final cleanup release before the strategy review. Subsequent
releases should focus on data analysis and parameter tuning — not code
hygiene.

---

## v1.7.2 — 2026-04-29

**Deeper cleanup pass.** Round 2 of dead-code reduction after v1.7.1
deployed cleanly. Found genuinely orphaned code beyond what pyflakes
catches by checking for unused private functions and unused module-level
exports.

### Removed orphan Telegram templates

The following template functions were defined in `telegram_templates.py`
but never imported or called anywhere in the codebase. bot.py uses inline
f-strings for these alerts (see lines around 1296-1322 in bot.py):

- `msg_daily_cap()` — daily cap reached alert
- `msg_new_day_resume()` — new trading day notice
- `msg_session_cap()` — session loss cap reached
- `msg_session_open()` — single-pair session open card (Zen uses the
  multi-pair version `msg_session_open_multi` instead)

Replaced with a placeholder comment block referencing this CHANGELOG entry
in case any of these are needed again. ~70 lines removed.

### Removed orphan helper

- `bot.py` `_next_day_reset_sgt()` — was likely used by `msg_daily_cap`
  to format the "Resets:" line. With that template gone, the helper is
  dead. ~10 lines removed.

### Updated stale docstring

- `signals.py` module docstring still claimed `TP: 30 pips · SL: 20 pips`
  globally. After v1.7's per-pair split, that's only true for EUR/GBP.
  Now correctly states EUR/GBP TP30/SL20 and AUD/USD TP22/SL15.
  Also removed v1.1/v1.2 micro-changelog lines that belong in CHANGELOG.md
  not in module docstrings.

### Verification

- All 16 Python files compile cleanly.
- `pyflakes` warnings: still 9 (all stylistic f-strings without
  placeholders — same as v1.7.1, no new issues introduced).
- All trading logic, strategy parameters, BE behavior, weekend close,
  and session handling unchanged.
- Used `_session_icon()` helper still active (4 call sites).
- Multi-pair `msg_session_open_multi()` still active (the one Zen uses).

### What this release does NOT change

- TP/SL/BE values per pair — same as v1.7.0/v1.7.1
- Two-step breakeven logic — unchanged
- Weekend close — unchanged
- Strategy / scoring / execution — unchanged
- Reports, reconciliation, telemetry — unchanged

Strategy review remains scheduled for ~30-trade milestone (mid-May).

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


## v2.0 TP/SL/BE/RR Reliability Fix

This build keeps the v1.9 strategy settings unchanged but hardens trade management:

- Saves the real OANDA `orderFillTransaction.tradeOpened.tradeID` instead of the fill transaction ID. This is required for break-even SL modification and P&L reconciliation via `/trades/{trade_id}`.
- Keeps TP/SL attached on fill using OANDA `takeProfitOnFill` and `stopLossOnFill`.
- Adds a final execution-side RR guard using `min_rr_ratio` before any order is sent.
- Recalculates actual estimated risk/reward after margin scaling or margin-reject retry.
- Aligns EUR/GBP fallback `pip_value_usd` with settings/docs at `13.5`.

### Current TP/SL/BE/RR settings

| Pair | SL | TP | RR | BE Step 1 | BE Step 2 |
|---|---:|---:|---:|---|---|
| EUR/GBP | 20 pips | 30 pips | 1.50 | +15p trigger, lock +3p | +25p trigger, lock +13p |
| AUD/USD | 15 pips | 22 pips | 1.47 | +11p trigger, lock +3p | +18p trigger, lock +10p |
