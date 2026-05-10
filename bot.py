"""Main orchestrator for Zen Scalp v1.9 — EUR/GBP + AUD/USD M15 BB+RSI Mean Reversion

Dual-pair (EUR/GBP + AUD/USD) M15 mean-reversion bot using Bollinger Bands + RSI
+ CPR pivot bias. Asian-session primary; London secondary.

Active sessions (SGT):
  Tokyo  08:00–15:59  threshold ≥4/6  cap 6   ← PRIMARY
  London 16:00–20:59  threshold ≥4/6  cap 6   ← SECONDARY
Disabled:
  US session    21:00–23:59  (historical 0% WR)
  US continuation 00:00–03:59 (trending hours)

All configuration lives in settings.json. Per-pair `pair_sl_tp` overrides
each pair's TP/SL/BE — EUR/GBP uses TP30/SL20, AUD/USD uses TP22/SL15
(per-pair split, v1.7+).

Architecture:
  run_bot_cycle() loops over every enabled pair each 5-minute cycle.
  For each pair it runs three phases:
    _guard_phase()      — pre-trade checks (session, caps, cooldown, OANDA,
                          weekend close, news filter)
    _signal_phase()     — BB + RSI scoring, sizing, margin guard
    _execution_phase()  — order placement and history persistence

  Two-step trailing breakeven runs in management slot of every cycle
  (see check_breakeven). Weekend force-close runs on Fridays from
  weekend_close_hour_sgt SGT (see force_close_for_weekend).

State isolation:
  Trade history (trade_history.json) is shared; queries filter by "instrument".
  Signal cache, ops state, and cooldown state are per-pair files, e.g.:
    score_cache_eurgbp.json / ops_state_eurgbp.json / runtime_pair_eurgbp.json
    score_cache_audusd.json / ops_state_audusd.json / runtime_pair_audusd.json
  Cross-pair dedup (e.g. session-open card) uses ops_state_global.json.
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytz

from calendar_fetcher import run_fetch as refresh_calendar
from config_loader import get_bool_env, load_settings
from database import Database
from logging_utils import configure_logging, get_logger
from news_filter import NewsFilter
from oanda_trader import OandaTrader
from signals import SignalEngine, score_to_position_usd
from startup_checks import run_startup_checks
from state_utils import (
    RUNTIME_STATE_FILE, SCORE_CACHE_FILE, OPS_STATE_FILE, TRADE_HISTORY_FILE,
    update_runtime_state, load_json, save_json, parse_sgt_timestamp,
)
from telegram_alert import TelegramAlert
from telegram_templates import (
    msg_signal_update, msg_trade_opened, msg_breakeven, msg_trade_closed,
    msg_news_block, msg_news_penalty, msg_cooldown_started,
    msg_spread_skip, msg_order_failed, msg_error, msg_friday_cutoff,
    msg_weekend_close, msg_trading_window_closed,
    msg_margin_adjustment,
    msg_session_open_multi,
)
from reconcile_state import reconcile_runtime_state, startup_oanda_reconcile

configure_logging()
log = get_logger(__name__)

SGT          = pytz.timezone("Asia/Singapore")
HISTORY_FILE = TRADE_HISTORY_FILE

_startup_reconcile_done: bool = False

SESSION_BANNERS = {
    "London": "🇬🇧 LONDON",
    "US":     "🗽 US",
    "Tokyo":  "🗼 TOKYO",
}


def _build_sessions(settings: dict) -> list:
    """Build the SESSIONS list from settings.
    US windows excluded when us_session_start_hour >= 99 (disabled sentinel).
    Tuple format: (name, macro, start_hour, end_hour, fallback_threshold).
    """
    lon_s  = int(settings.get("london_session_start_hour",    16))
    lon_e  = int(settings.get("london_session_end_hour",      20))
    us_s   = int(settings.get("us_session_start_hour",        21))
    us_e   = int(settings.get("us_session_end_hour",          23))
    us_e2  = int(settings.get("us_session_early_end_hour",     3))
    tok_s  = int(settings.get("tokyo_session_start_hour",      8))
    tok_e  = int(settings.get("tokyo_session_end_hour",       15))
    sessions = [
        ("Tokyo Window",  "Tokyo",  tok_s, tok_e, 5),
        ("London Window", "London", lon_s, lon_e, 4),
    ]
    if us_s  < 99: sessions.append(("US Window", "US", us_s,  us_e,  4))
    if us_e2 < 99: sessions.append(("US Window", "US", 0,     us_e2, 3))
    return sessions


# ── Pair helpers ──────────────────────────────────────────────────────────────

def get_enabled_pairs(settings: dict) -> list[tuple[str, dict]]:
    """Return [(instrument, pair_cfg), ...] for all enabled pairs in order."""
    pairs = settings.get("pairs", {})
    return [
        (instr, cfg)
        for instr, cfg in pairs.items()
        if isinstance(cfg, dict) and cfg.get("enabled", True)
    ]


def get_effective_settings(global_s: dict, pair_cfg: dict) -> dict:
    """Merge global settings with pair-specific overrides.
    Pair values take priority; everything else falls back to global.
    """
    merged = dict(global_s)
    merged.update(pair_cfg)
    return merged


def _pair_key(instrument: str) -> str:
    """'EUR_GBP' → 'eurgbp', 'AUD_USD' → 'audusd' (used as file-name suffix)."""
    return instrument.lower().replace("_", "")


def _pretty_pair(instrument: str) -> str:
    """Format pair for user-facing strings: 'EUR_GBP' → 'EUR/GBP'.

    Used in Telegram alerts. Log lines and DB rows keep the OANDA-native
    underscore format (EUR_GBP) since those are programmatic.
    """
    return instrument.replace("_", "/")


def _pair_state_file(base: Path, instrument: str) -> Path:
    """Return a per-pair variant of a state file.
    e.g. score_cache.json → score_cache_gbpusd.json
    """
    return base.parent / f"{base.stem}_{_pair_key(instrument)}{base.suffix}"


def _pair_runtime_file(instrument: str) -> Path:
    """Per-pair runtime state (cooldown, last SL close)."""
    return RUNTIME_STATE_FILE.parent / f"runtime_pair_{_pair_key(instrument)}.json"


def _pip_size(settings: dict) -> float:
    return float(settings.get("pip_size", 0.0001) or 0.0001)


def _pip_dp(pip: float) -> int:
    """Decimal places for price rounding given pip size."""
    if pip <= 0.0001: return 5   # EUR_GBP (Zen)
    if pip <= 0.01:   return 3   # JPY pairs (not used in Zen Scalp v1.9)
    return 2


# ── Trading day ───────────────────────────────────────────────────────────────

def get_trading_day(now_sgt: datetime, day_start_hour: int = 8) -> str:
    if now_sgt.hour < day_start_hour:
        return (now_sgt - timedelta(days=1)).strftime("%Y-%m-%d")
    return now_sgt.strftime("%Y-%m-%d")


def _clean_reason(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "No reason available"
    for part in reversed([p.strip() for p in text.split("|") if p.strip()]):
        plain = re.sub(r"^[^A-Za-z0-9]+", "", part).strip()
        if plain:
            return plain[:120]
    return text[:120]


def _build_signal_checks(score, direction, rr_ratio=None, tp_pct=None,
                         spread_pips=None, spread_limit=None, session_ok=True,
                         news_ok=True, open_trade_ok=True, margin_ok=None,
                         cooldown_ok=True, signal_threshold=4, min_rr_ratio=1.4):
    mandatory_checks = [
        (f"Score >= {signal_threshold}",
         score >= signal_threshold and direction != "NONE", f"{score}/6"),
        (f"RR >= {min_rr_ratio:.1f}",
         None if rr_ratio is None else rr_ratio >= min_rr_ratio,
         "n/a" if rr_ratio is None else f"{rr_ratio:.2f}"),
    ]
    quality_checks = [
        ("TP >= 0.35%", None if tp_pct is None else tp_pct >= 0.35,
         "n/a" if tp_pct is None else f"{tp_pct:.4f}%"),
    ]
    execution_checks = [
        ("Session active",  session_ok,    "active"  if session_ok   else "inactive"),
        ("News clear",      news_ok,       "clear"   if news_ok      else "blocked"),
        ("Cooldown clear",  cooldown_ok,   "clear"   if cooldown_ok  else "active"),
        ("No open trade",   open_trade_ok, "ready"   if open_trade_ok else "existing position"),
        ("Spread OK",
         None if spread_pips is None or spread_limit is None
              else spread_pips <= spread_limit,
         "n/a" if spread_pips is None or spread_limit is None
              else f"{spread_pips}/{spread_limit} pips"),
        ("Margin OK", margin_ok,
         "n/a" if margin_ok is None else ("pass" if margin_ok else "insufficient")),
    ]
    return mandatory_checks, quality_checks, execution_checks


def _signal_payload(**kwargs):
    mc, qc, ec = _build_signal_checks(**kwargs)
    return {"mandatory_checks": mc, "quality_checks": qc, "execution_checks": ec}


# ── Settings ──────────────────────────────────────────────────────────────────

def validate_settings(settings: dict) -> dict:
    required = ["pairs"]  # Zen Scalp v1.9: pair_sl_tp fixed pips used exclusively
    missing  = [k for k in required if k not in settings]
    if missing:
        raise ValueError(f"Missing required settings keys: {missing}")

    settings.setdefault("signal_threshold",           4)
    # 2.4% risk/trade on $2,000 account (full $48) / 1.5% (partial $30)
    settings.setdefault("position_full_usd",          60)
    settings.setdefault("position_partial_usd",       45)
    settings.setdefault("account_balance_override",   0)
    settings.setdefault("enabled",                    True)
    settings.setdefault("pip_size",                   0.0001)
    # be_trigger_pips: break-even trigger in pips
    settings.setdefault("be_trigger_pips",             20)
    # be_lock_pips (v1.5): pips of profit to lock when BE fires.
    # 0 = exit at entry price (classic BE, net slightly negative after spread).
    # 3 = move SL past entry by 3 pips in trade's favor (~2p net after typical 1p spread).
    settings.setdefault("be_lock_pips",                0)
    # be_step2_* (v1.7): second-stage trailing breakeven. After Step 1 fires at
    # be_trigger_pips and locks be_lock_pips, if MFE continues to be_step2_trigger_pips
    # the SL is moved further to lock be_step2_lock_pips. Captures the "got close
    # to TP but reverted" outcomes without giving up the runner.
    settings.setdefault("be_step2_enabled",            True)
    settings.setdefault("be_step2_trigger_pips",       0)   # 0 disables Step 2
    settings.setdefault("be_step2_lock_pips",          0)
    settings.setdefault("trading_day_start_hour_sgt", 8)
    settings.setdefault("max_losing_trades_session",  4)
    settings.setdefault("margin_safety_factor",       0.6)
    settings.setdefault("margin_retry_safety_factor", 0.4)
    settings.setdefault("margin_rate_override",       0.0)
    settings.setdefault("auto_scale_on_margin_reject",True)
    settings.setdefault("telegram_show_margin",       True)
    # Suppress WATCHING alerts for signals below this score (0 = send all)
    settings.setdefault("telegram_min_score_alert",   3)
    settings.setdefault("friday_cutoff_hour_sgt",     23)
    settings.setdefault("friday_cutoff_minute_sgt",   0)
    # v1.6.1: weekend force-close — protects against gap risk over the weekend.
    # By Fri weekend_close_hour:weekend_close_minute SGT, all open trades are
    # closed at market regardless of P&L. This is risk management, not strategy.
    settings.setdefault("weekend_close_enabled",      True)
    settings.setdefault("weekend_close_hour_sgt",     22)
    settings.setdefault("weekend_close_minute_sgt",   0)
    settings.setdefault("news_lookahead_min",         120)
    settings.setdefault("news_medium_penalty_score",  -1)
    settings.setdefault("loss_streak_cooldown_min",   30)
    settings.setdefault("min_rr_ratio",               1.4)
    settings.setdefault("rr_ratio",                   1.67)  # fallback only — pair_sl_tp always used
    settings.setdefault("calendar_prune_days_ahead",  21)
    settings.setdefault("startup_dedup_seconds",      90)
    settings.setdefault("spread_limits",              {"London": 5, "US": 5})
    settings.setdefault("max_trades_day",             20)
    settings.setdefault("max_losing_trades_day",      8)
    settings.setdefault("max_trades_london",          10)
    settings.setdefault("max_trades_us",              10)
    # session window hours
    settings.setdefault("london_session_start_hour",  16)
    settings.setdefault("london_session_end_hour",    20)
    settings.setdefault("us_session_start_hour",      99)  # disabled — historical 0% WR
    settings.setdefault("us_session_end_hour",        99)  # disabled
    settings.setdefault("us_session_early_end_hour",  99)  # US continuation disabled
    settings.setdefault("dead_zone_start_hour",        4)   # 04:00 SGT — pre-Tokyo gap
    settings.setdefault("dead_zone_end_hour",           7)   # 07:59 SGT end
    # report schedule times (SGT)
    settings.setdefault("daily_report_hour_sgt",       7)   # Mon–Fri 07:50 SGT
    settings.setdefault("daily_report_minute_sgt",     50)
    settings.setdefault("weekly_report_hour_sgt",      8)
    settings.setdefault("weekly_report_minute_sgt",   0)
    settings.setdefault("monthly_report_hour_sgt",     8)
    settings.setdefault("monthly_report_minute_sgt",   10)
    # Tokyo/Asian session
    settings.setdefault("tokyo_session_start_hour",    8)
    settings.setdefault("tokyo_session_end_hour",     15)
    settings.setdefault("max_trades_tokyo",            6)  # Asian primary — same cap as London
    # global concurrent-trade cap (0 = per-pair limits only)
    settings.setdefault("max_total_open_trades",       2)
    # TP2 reference RR multiplier for the trade opened Telegram alert
    settings.setdefault("tp2_rr_reference",            3.0)
    # minimum units after margin guard — reject micro-orders gracefully
    settings.setdefault("min_trade_units",           1000)
    # EUR/GBP + AUD/USD only
    # v2.2: pip_value_usd is in account-home-currency per 100k units per pip.
    # Account home = SGD, so values include the home-currency conversion:
    #   AUD/USD (USD-quoted): 10.0 USD/pip × ~1.29 SGD/USD = ~12.9 SGD/pip
    #   EUR/GBP (GBP-quoted): 13.5 USD/pip × ~1.29 SGD/USD = ~17.4 SGD/pip
    # Update if SGD/USD or GBP/SGD moves materially (±0.05 from these levels).
    settings.setdefault("pair_sl_tp", {
        "EUR_GBP": {"sl_pips": 20, "tp_pips": 30, "pip_value_usd": 17.4,
                    "be_trigger_pips": 15, "be_lock_pips": 3,
                    "be_step2_trigger_pips": 25, "be_step2_lock_pips": 13},
        "AUD_USD": {"sl_pips": 15, "tp_pips": 22, "pip_value_usd": 12.9,
                    "be_trigger_pips": 11, "be_lock_pips": 3,
                    "be_step2_trigger_pips": 18, "be_step2_lock_pips": 10},
    })
    # dead zone = pre-Tokyo gap 04:00–07:59 SGT (overrides any stale setdefault above)
    settings["dead_zone_start_hour"] = int(settings.get("dead_zone_start_hour", 4))
    settings["dead_zone_end_hour"]   = int(settings.get("dead_zone_end_hour",   7))
    # Ensure Tokyo threshold is present in session_thresholds
    st = settings.setdefault("session_thresholds", {})
    st.setdefault("Tokyo", 5)

    if int(settings.get("loss_streak_cooldown_min", 30)) < 0:
        raise ValueError("loss_streak_cooldown_min must be >= 0")

    return settings


def is_friday_cutoff(now_sgt: datetime, settings: dict) -> bool:
    if now_sgt.weekday() != 4:
        return False
    ch = int(settings.get("friday_cutoff_hour_sgt", 23))
    cm = int(settings.get("friday_cutoff_minute_sgt", 0))
    return now_sgt.hour > ch or (now_sgt.hour == ch and now_sgt.minute >= cm)


def is_weekend_close_time(now_sgt: datetime, settings: dict) -> bool:
    """Return True when bot should force-close all open positions for the weekend.

    Fires on Fridays from `weekend_close_hour_sgt:weekend_close_minute_sgt` SGT
    onwards. Independent from `is_friday_cutoff` — the latter only blocks NEW
    entries. This protects against weekend gap risk on EXISTING positions.
    """
    if not settings.get("weekend_close_enabled", True):
        return False
    if now_sgt.weekday() != 4:  # 4 = Friday
        return False
    ch = int(settings.get("weekend_close_hour_sgt", 22))
    cm = int(settings.get("weekend_close_minute_sgt", 0))
    return now_sgt.hour > ch or (now_sgt.hour == ch and now_sgt.minute >= cm)


# ── Trade history helpers ──────────────────────────────────────────────────────

def load_history() -> list:
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(history: list):
    save_json(HISTORY_FILE, history)


def prune_old_trades(history: list, settings: dict | None = None) -> list:
    retention_days = int((settings or {}).get("db_retention_days", 90))
    cutoff = datetime.now(SGT) - timedelta(days=retention_days)
    active, pruned = [], 0
    for trade in history:
        ts = trade.get("timestamp_sgt", "")
        try:
            dt = SGT.localize(datetime.strptime(ts, "%Y-%m-%d %H:%M:%S"))
            if dt < cutoff:
                pruned += 1
            else:
                active.append(trade)
        except Exception:
            active.append(trade)
    if pruned:
        log.info("Pruned %d trade(s) older than %d days | Active: %d",
                 pruned, retention_days, len(active))
    return active


# ── Session helpers ────────────────────────────────────────────────────────────

def get_session(now: datetime, settings: dict = None):
    h       = now.hour
    s       = settings or {}
    st      = s.get("session_thresholds", {})
    sessions = _build_sessions(s)
    for name, macro, start, end, fallback_thr in sessions:
        if start <= h <= end:
            return name, macro, int(st.get(macro, fallback_thr))
    return None, None, None


def is_dead_zone_time(now_sgt: datetime, settings: dict | None = None) -> bool:
    s        = settings or {}
    dz_start = int(s.get("dead_zone_start_hour", 4))   # 04:00 SGT
    dz_end   = int(s.get("dead_zone_end_hour",   7))   # 07:59 SGT
    return dz_start <= now_sgt.hour <= dz_end


def get_window_key(session_name: str | None) -> str | None:
    if session_name == "London Window": return "London"
    if session_name == "US Window":     return "US"
    if session_name == "Tokyo Window":  return "Tokyo"
    return None


def get_window_trade_cap(window_key: str | None, settings: dict) -> int | None:
    if window_key == "London": return int(settings.get("max_trades_london", 10))
    if window_key == "US":     return int(settings.get("max_trades_us",     10))
    if window_key == "Tokyo":  return int(settings.get("max_trades_tokyo",   6))
    return None


def window_trade_count(history: list, today_str: str,
                       window_key: str, instrument: str) -> int:
    aliases = {
        "London": {"London", "London Window"},
        "US":     {"US", "US Window"},
        "Tokyo":  {"Tokyo", "Tokyo Window"},
    }
    valid = aliases.get(window_key, {window_key})
    return sum(
        1 for t in history
        if t.get("timestamp_sgt", "").startswith(today_str)
        and t.get("status") == "FILLED"
        and t.get("instrument") == instrument
        and (t.get("window") or t.get("session") or t.get("macro_session")) in valid
    )


def session_losses(history: list, today_str: str,
                   macro: str, instrument: str) -> int:
    aliases = {
        "London": {"London", "London Window"},
        "US":     {"US", "US Window"},
        "Tokyo":  {"Tokyo", "Tokyo Window"},
    }
    valid = aliases.get(macro, {macro})
    losses = 0
    for t in history:
        if not t.get("timestamp_sgt", "").startswith(today_str): continue
        if t.get("status") != "FILLED":                          continue
        if t.get("instrument") != instrument:                    continue
        tm = t.get("macro_session") or t.get("window") or t.get("session") or ""
        if tm not in valid:                                      continue
        pnl = t.get("realized_pnl_usd")
        if isinstance(pnl, (int, float)) and pnl < 0:
            losses += 1
    return losses


# ── Risk / daily cap helpers ───────────────────────────────────────────────────

def daily_totals(history: list, today_str: str,
                 trader=None, instrument: str = ""):
    """Count P&L / trades / losses for one instrument today."""
    pnl, count, losses = 0.0, 0, 0
    for t in history:
        if not t.get("timestamp_sgt", "").startswith(today_str): continue
        if t.get("status") != "FILLED":                          continue
        if instrument and t.get("instrument") != instrument:     continue
        count += 1
        p = t.get("realized_pnl_usd")
        if isinstance(p, (int, float)):
            pnl += p
            if p < 0: losses += 1
    if trader is not None and instrument:
        try:
            pos = trader.get_position(instrument)
            if pos:
                unrealized = trader.check_pnl(pos)
                pnl += unrealized
                if unrealized < 0: losses += 1
        except Exception as e:
            log.warning("Could not fetch unrealized P&L for %s: %s", instrument, e)
    return pnl, count, losses


def get_closed_trade_records_today(history: list, today_str: str,
                                    instrument: str = "") -> list:
    closed = [
        t for t in history
        if t.get("timestamp_sgt", "").startswith(today_str)
        and t.get("status") == "FILLED"
        and (not instrument or t.get("instrument") == instrument)
        and isinstance(t.get("realized_pnl_usd"), (int, float))
    ]
    closed.sort(key=lambda t: t.get("closed_at_sgt") or t.get("timestamp_sgt") or "")
    return closed


def consecutive_loss_streak_today(history: list, today_str: str,
                                   instrument: str = "") -> int:
    streak = 0
    for t in reversed(get_closed_trade_records_today(history, today_str, instrument)):
        pnl = t.get("realized_pnl_usd")
        if not isinstance(pnl, (int, float)): continue
        if pnl < 0: streak += 1
        else:       break
    return streak


_parse_sgt_timestamp = parse_sgt_timestamp


def maybe_start_loss_cooldown(history: list, today_str: str,
                               now_sgt: datetime, settings: dict,
                               instrument: str = ""):
    cooldown_min = int(settings.get("loss_streak_cooldown_min", 30))
    if cooldown_min <= 0:
        return None, None, 0
    streak = consecutive_loss_streak_today(history, today_str, instrument)
    if streak < 2:
        return None, None, streak
    closed = get_closed_trade_records_today(history, today_str, instrument)
    if len(closed) < 2:
        return None, None, streak
    trigger_trade  = closed[-1]
    trigger_marker = (
        trigger_trade.get("trade_id") or
        trigger_trade.get("closed_at_sgt") or
        trigger_trade.get("timestamp_sgt")
    )
    # Use per-pair runtime file so pairs don't share cooldown state
    rt_file = _pair_runtime_file(instrument) if instrument else RUNTIME_STATE_FILE
    rt      = load_json(rt_file, {})
    if rt.get("loss_cooldown_trigger") == trigger_marker:
        return _parse_sgt_timestamp(rt.get("cooldown_until_sgt")), trigger_marker, streak
    cooldown_until = now_sgt + timedelta(minutes=cooldown_min)
    save_json(rt_file, {
        **rt,
        "loss_cooldown_trigger": trigger_marker,
        "cooldown_until_sgt":    cooldown_until.strftime("%Y-%m-%d %H:%M:%S"),
        "cooldown_reason":       f"{streak} consecutive losses",
        "updated_at_sgt":        now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
    })
    return cooldown_until, trigger_marker, streak


def active_cooldown_until(now_sgt: datetime, instrument: str = ""):
    rt_file = _pair_runtime_file(instrument) if instrument else RUNTIME_STATE_FILE
    rt = load_json(rt_file, {})
    cu = _parse_sgt_timestamp(rt.get("cooldown_until_sgt"))
    return cu if cu and now_sgt < cu else None


# ── Position sizing ───────────────────────────────────────────────────────────

def compute_sl_usd(levels: dict, settings: dict) -> float:
    """SL price-distance for order placement. pair_sl_tp always provides sl_price_dist."""
    dist = levels.get("sl_price_dist")
    if dist is not None:
        try:
            v = float(dist)
            if v > 0:
                log.debug("Signal SL (price_dist): %.6f", v)
                return v
        except (TypeError, ValueError):
            pass
    rec = levels.get("sl_usd_rec")
    if rec is not None:
        try:
            v = float(rec)
            if v > 0:
                log.debug("Signal SL (sl_usd_rec): %.6f", v)
                return v
        except (TypeError, ValueError):
            pass
    pip = float(levels.get("pip_size", 0.0001))
    log.warning("compute_sl_usd: no valid SL in levels — using 18p emergency fallback")
    return round(18 * pip, 7)


def compute_tp_usd(levels: dict, sl_usd: float, settings: dict) -> float:
    """TP price-distance for order placement. pair_sl_tp always provides tp_price_dist."""
    dist = levels.get("tp_price_dist")
    if dist is not None:
        try:
            v = float(dist)
            if v > 0: return v
        except (TypeError, ValueError):
            pass
    rec = levels.get("tp_usd_rec")
    if rec is not None:
        try:
            v = float(rec)
            if v > 0: return v
        except (TypeError, ValueError):
            pass
    return round(sl_usd * 1.5, 8)   # 1.5x RR emergency fallback


def derive_rr_ratio(levels: dict, sl_usd: float, tp_usd: float, settings: dict) -> float:
    try:
        rr = float(levels.get("rr_ratio"))
        if rr > 0: return rr
    except (TypeError, ValueError):
        pass
    if sl_usd > 0 and tp_usd > 0:
        return round(tp_usd / sl_usd, 2)
    return float(settings.get("rr_ratio", 2.5))


def calculate_units_from_position(position_usd: int, sl_usd: float) -> float:
    """units = position_usd / sl_usd_rec (USD-adjusted risk per unit).

    sl_usd_rec from signals.py already applies pip_value_usd for GBP-quoted
    pairs (EUR/GBP), giving true USD risk. AUD/USD is USD-quoted so sl_usd_rec
    and sl_price_dist are numerically equivalent.
    """
    if sl_usd <= 0 or position_usd <= 0:
        return 0.0
    return round(position_usd / sl_usd, 2)


def apply_margin_guard(trader, instrument: str, requested_units: float,
                       entry_price: float, free_margin: float,
                       settings: dict) -> tuple[float, dict]:
    """Floor requested units against available margin."""
    margin_safety       = float(settings.get("margin_safety_factor",       0.6))
    margin_retry_safety = float(settings.get("margin_retry_safety_factor", 0.4))
    specs = trader.get_instrument_specs(instrument)

    # Use per-pair margin_rate_override (0.0 = use broker's own rate)
    override    = float(settings.get("margin_rate_override", 0.0) or 0.0)
    broker_rate = float(specs.get("marginRate", 0.02) or 0.02)
    margin_rate = max(broker_rate, override)

    norm_req = trader.normalize_units(instrument, requested_units)
    req_marg = trader.estimate_required_margin(instrument, norm_req, entry_price)

    if free_margin <= 0 or entry_price <= 0 or margin_rate <= 0:
        return 0.0, {"status": "SKIP", "reason": "invalid_margin_context",
                     "free_margin": float(free_margin or 0),
                     "required_margin": req_marg,
                     "requested_units": norm_req, "final_units": 0.0}

    max_units   = (free_margin * margin_safety) / (entry_price * margin_rate)
    norm_capped = trader.normalize_units(instrument, min(norm_req, max_units))
    fin_marg    = trader.estimate_required_margin(instrument, norm_capped, entry_price)
    status = "NORMAL" if abs(norm_capped - norm_req) < 1e-9 else "ADJUSTED"

    if norm_capped <= 0:
        retry = trader.normalize_units(
            instrument,
            (free_margin * margin_retry_safety) / (entry_price * margin_rate))
        ret_m = trader.estimate_required_margin(instrument, retry, entry_price)
        if retry > 0:
            return retry, {"status": "ADJUSTED", "reason": "margin_retry_floor",
                           "free_margin": float(free_margin), "required_margin": ret_m,
                           "requested_units": norm_req, "final_units": retry}
        return 0.0, {"status": "SKIP", "reason": "insufficient_margin",
                     "free_margin": float(free_margin), "required_margin": req_marg,
                     "requested_units": norm_req, "final_units": 0.0}

    return norm_capped, {"status": status, "reason": "margin_guard" if status == "ADJUSTED" else "ok",
                         "free_margin": float(free_margin), "required_margin": fin_marg,
                         "requested_units": norm_req, "final_units": norm_capped}


def compute_sl_tp_pips(sl_usd: float, tp_usd: float, pip_size: float = 0.0001):
    """Convert price-distance SL/TP to pips for the OANDA place_order API."""
    return round(sl_usd / pip_size), round(tp_usd / pip_size)


def compute_sl_tp_prices(entry: float, direction: str,
                          sl_usd: float, tp_usd: float,
                          dp: int = 5):
    """Return (sl_price, tp_price) rounded to the correct decimal places."""
    if direction == "BUY":
        return round(entry - sl_usd, dp), round(entry + tp_usd, dp)
    return round(entry + sl_usd, dp), round(entry - tp_usd, dp)


def get_effective_balance(balance: float | None, settings: dict) -> float:
    override = settings.get("account_balance_override")
    if override is not None:
        try:
            v = float(override)
            if v > 0: return v
        except (TypeError, ValueError):
            pass
    return float(balance or 0)


# ── Per-pair cache helpers ────────────────────────────────────────────────────

def load_signal_cache(instrument: str) -> dict:
    f = _pair_state_file(SCORE_CACHE_FILE, instrument)
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception:
        return {}


def save_signal_cache(cache: dict, instrument: str):
    save_json(_pair_state_file(SCORE_CACHE_FILE, instrument), cache)


def load_ops_state(instrument: str) -> dict:
    f = _pair_state_file(OPS_STATE_FILE, instrument)
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception:
        return {}


def save_ops_state(state: dict, instrument: str):
    save_json(_pair_state_file(OPS_STATE_FILE, instrument), state)


# v1.7: global (cross-pair) session-open dedup. The session-open Telegram card
# combines stats from all pairs into a single message — so it must only fire
# once per session per day, regardless of which pair's cycle reached the check
# first. The per-pair `OPS_STATE_FILE` couldn't enforce that (each pair had its
# own file → both fired their own copy).
_GLOBAL_OPS_FILE = OPS_STATE_FILE.parent / "ops_state_global.json"

def load_global_ops_state() -> dict:
    try:
        return json.loads(_GLOBAL_OPS_FILE.read_text(encoding="utf-8")) if _GLOBAL_OPS_FILE.exists() else {}
    except Exception:
        return {}

def save_global_ops_state(state: dict):
    save_json(_GLOBAL_OPS_FILE, state)

def send_once_global(alert, key: str, value: str, message: str):
    """Send `message` only if `key` doesn't already map to `value` in the
    global ops state file. Used for cross-pair dedup of session-open cards."""
    state = load_global_ops_state()
    if state.get(key) != value:
        alert.send(message)
        state[key] = value
        save_global_ops_state(state)


def send_once_per_state(alert, cache: dict, key: str, value: str,
                        message: str, instrument: str):
    if cache.get(key) != value:
        alert.send(message)
        cache[key] = value
        save_ops_state(cache, instrument)


# ── Break-even management ──────────────────────────────────────────────────────

def check_breakeven(history: list, trader, alert, settings: dict, instrument: str):
    """Two-step trailing break-even (v1.7).

    Step 1: when MFE reaches `be_trigger_pips`, move SL past entry by
            `be_lock_pips` in the trade's favor. Locks ~lock_pips−1 net after
            typical 1p spread.

    Step 2 (NEW v1.7): when MFE further reaches `be_step2_trigger_pips`, move
            SL again to lock `be_step2_lock_pips`. Captures the "near-TP
            revert" pattern without capping the TP runner.

    State: `breakeven_step` int on each trade (0 = untouched, 1 = step 1
    fired, 2 = step 2 fired). Backward compatible — old trades with
    `breakeven_moved: True` are treated as `breakeven_step: 1`.

    Resolution order for all values: pair_sl_tp[PAIR] override → global setting
    → hard default. Step 2 disables itself if `be_step2_enabled: false` or if
    the trigger value is 0 / unset (so the function gracefully degrades to
    single-step BE for pairs without Step 2 configured).

    Direction math is unchanged from v1.5:
        BUY :  new_sl = entry + (lock_pips × pip_size)
        SELL:  new_sl = entry − (lock_pips × pip_size)
    """
    demo  = settings.get("demo_mode", True)
    _ps   = _pip_size(settings)
    _dp   = _pip_dp(_ps)

    # Per-pair config with global fallback
    _pair_cfg = settings.get("pair_sl_tp", {}).get(instrument, {})

    # Step 1
    s1_trigger_pips = int(_pair_cfg.get("be_trigger_pips",
                          settings.get("be_trigger_pips", 20)))
    s1_lock_pips    = int(_pair_cfg.get("be_lock_pips",
                          settings.get("be_lock_pips", 0)))

    # Step 2 — feature flag + trigger value both must be > 0 to enable
    s2_enabled      = bool(settings.get("be_step2_enabled", True))
    s2_trigger_pips = int(_pair_cfg.get("be_step2_trigger_pips",
                          settings.get("be_step2_trigger_pips", 0)))
    s2_lock_pips    = int(_pair_cfg.get("be_step2_lock_pips",
                          settings.get("be_step2_lock_pips", 0)))
    step2_active    = s2_enabled and s2_trigger_pips > 0 and s2_lock_pips > 0

    # Sanity guard: Step 2 must trigger after Step 1 and lock more than Step 1
    if step2_active and (s2_trigger_pips <= s1_trigger_pips
                         or s2_lock_pips <= s1_lock_pips):
        log.warning("[%s] Step 2 BE config invalid (s1=%d/+%d, s2=%d/+%d). "
                    "Step 2 disabled for this pair.",
                    instrument, s1_trigger_pips, s1_lock_pips,
                    s2_trigger_pips, s2_lock_pips)
        step2_active = False

    changed = False

    for trade in history:
        if trade.get("status")     != "FILLED":   continue
        if trade.get("instrument") != instrument: continue

        # Backward-compat: derive step from legacy breakeven_moved flag
        current_step = trade.get("breakeven_step")
        if current_step is None:
            current_step = 1 if trade.get("breakeven_moved") else 0

        # Already at final step → nothing to do
        if current_step >= 2:                    continue
        if current_step >= 1 and not step2_active:
            continue

        trade_id  = trade.get("trade_id")
        entry     = trade.get("entry")
        direction = trade.get("direction", "")
        if not trade_id or not entry or direction not in ("BUY", "SELL"):
            continue

        open_trade = trader.get_open_trade(str(trade_id))
        if open_trade is None: continue

        mid, bid, ask = trader.get_price(instrument)
        if mid is None: continue

        current_price = bid if direction == "BUY" else ask

        # Determine which step to attempt next
        if current_step == 0:
            target_trigger_pips = s1_trigger_pips
            target_lock_pips    = s1_lock_pips
            target_step         = 1
        else:  # current_step == 1, step2_active is True (we already returned otherwise)
            target_trigger_pips = s2_trigger_pips
            target_lock_pips    = s2_lock_pips
            target_step         = 2

        trigger_dist = target_trigger_pips * _ps
        lock_dist    = target_lock_pips    * _ps
        trigger_price = (entry + trigger_dist if direction == "BUY"
                         else entry - trigger_dist)
        triggered = (
            (direction == "BUY"  and current_price >= trigger_price) or
            (direction == "SELL" and current_price <= trigger_price)
        )
        if not triggered: continue

        new_sl_price = (entry + lock_dist if direction == "BUY"
                        else entry - lock_dist)

        result = trader.modify_sl(str(trade_id), float(new_sl_price), instrument=instrument)
        if result.get("success"):
            trade["breakeven_step"]  = target_step
            trade["breakeven_moved"] = True   # legacy flag kept for backward compat
            trade["be_locked_pips"]  = target_lock_pips
            changed = True
            try:
                unrealized_pnl = float(open_trade.get("unrealizedPL", 0))
            except Exception:
                unrealized_pnl = 0
            log.info("BE Step %d | %s trade=%s entry=%.*f new_sl=%.*f lock=+%dp (trigger=%.*f, +%dp)",
                     target_step, instrument, trade_id,
                     _dp, entry, _dp, new_sl_price, target_lock_pips,
                     _dp, trigger_price, target_trigger_pips)
            alert.send(msg_breakeven(
                trade_id=trade_id, direction=direction, entry=entry,
                trigger_price=trigger_price, trigger_dist=trigger_dist,
                current_price=current_price, unrealized_pnl=unrealized_pnl,
                demo=demo, price_dp=_dp,
                new_sl_price=new_sl_price, lock_pips=target_lock_pips,
                step=target_step,
            ))
        else:
            log.warning("BE Step %d failed for %s trade %s: %s",
                        target_step, instrument, trade_id, result.get("error"))

    if changed:
        save_history(history)


# ── Weekend force-close (v1.6.1) ──────────────────────────────────────────────

def force_close_for_weekend(history: list, trader, alert, settings: dict,
                             instrument: str, now_sgt: datetime) -> bool:
    """Force-close all open positions on the given instrument for weekend safety.

    Triggered every cycle once `is_weekend_close_time` returns True. Closes
    each open position via market order regardless of P&L, then sends a
    weekend-close Telegram alert. Idempotent — once a trade is closed at
    OANDA, subsequent cycles see no open position and skip cleanly.

    P&L reconciliation is left to the normal `backfill_pnl` flow on the next
    cycle, which finds the closed trade via OANDA's transaction stream and
    fills `realized_pnl_usd` + `closed_at_sgt` on the history record.

    Returns True if any close was attempted (used for logging/diagnostics).
    """
    _ps = _pip_size(settings)
    _dp = _pip_dp(_ps)
    demo = settings.get("demo_mode", True)
    cutoff_h = int(settings.get("weekend_close_hour_sgt", 22))
    cutoff_m = int(settings.get("weekend_close_minute_sgt", 0))

    open_trades = [
        t for t in history
        if t.get("status") == "FILLED"
        and t.get("instrument") == instrument
        and t.get("realized_pnl_usd") is None
    ]
    if not open_trades:
        return False

    attempted = False
    for trade in open_trades:
        trade_id  = trade.get("trade_id")
        direction = trade.get("direction", "")
        entry     = trade.get("entry")
        if not trade_id:
            continue

        # Verify trade is still open at OANDA before closing
        oanda_trade = trader.get_open_trade(str(trade_id))
        if oanda_trade is None:
            continue  # already closed (TP/SL/BE) — nothing to do

        # Capture floating P&L for the alert before closing
        try:
            unrealized_pnl = float(oanda_trade.get("unrealizedPL", 0))
        except Exception:
            unrealized_pnl = 0.0

        try:
            mid, bid, ask = trader.get_price(instrument)
            current_price = (bid if direction == "BUY" else ask) if mid is not None else None
        except Exception:
            current_price = None

        # Compute current pips for the alert
        pips_now = None
        if entry and current_price:
            if direction == "BUY":
                pips_now = round((current_price - entry) / _ps, 1)
            elif direction == "SELL":
                pips_now = round((entry - current_price) / _ps, 1)

        attempted = True
        log.info("Weekend close | %s trade=%s entry=%.*f current=%s pips=%s pnl=$%+.2f",
                 instrument, trade_id, _dp, entry or 0,
                 f"{current_price:.{_dp}f}" if current_price else "n/a",
                 f"{pips_now:+.1f}" if pips_now is not None else "n/a",
                 unrealized_pnl)

        result = trader.close_position(instrument)
        if result.get("success"):
            alert.send(msg_weekend_close(
                trade_id=trade_id, direction=direction, instrument=instrument,
                entry=entry, close_price=current_price, pips=pips_now,
                pnl=unrealized_pnl, cutoff_hour=cutoff_h, cutoff_minute=cutoff_m,
                demo=demo, price_dp=_dp,
            ))
        else:
            log.warning("Weekend close failed for %s trade %s: %s — will retry next cycle",
                        instrument, trade_id, result.get("error"))

    return attempted


# ── Max pips tracker ────────────────────────────────────────────────────

def track_max_pips(history: list, trader, settings: dict, instrument: str) -> bool:
    """Track the maximum pip distance reached by each open trade.

    Called every cycle. For each open trade, fetches current price and
    computes pips moved in trade direction. Stores the peak in
    trade["max_pips_reached"] — survives until trade closes.

    Used post-trade to compare actual TP hit vs maximum reachable profit,
    informing TP level calibration decisions.
    """
    _pip_size_val = _pip_size(settings)
    changed       = False

    for trade in history:
        if trade.get("status")     != "FILLED":   continue
        if trade.get("instrument") != instrument: continue
        if trade.get("realized_pnl_usd") is not None: continue  # already closed

        entry     = trade.get("entry")
        direction = trade.get("direction", "")
        if not entry or direction not in ("BUY", "SELL"):
            continue

        try:
            mid, bid, ask = trader.get_price(instrument)
            if mid is None:
                continue
            current_price = bid if direction == "BUY" else ask
            if direction == "BUY":
                pips_now = round((current_price - entry) / _pip_size_val, 1)
            else:
                pips_now = round((entry - current_price) / _pip_size_val, 1)

            prev_max = trade.get("max_pips_reached", -999)
            if pips_now > prev_max:
                trade["max_pips_reached"] = round(pips_now, 1)
                changed = True
        except Exception as exc:
            log.debug("track_max_pips error for %s: %s", instrument, exc)

    return changed


# ── PnL backfill ───────────────────────────────────────────────────────────────

def backfill_pnl(history: list, trader, alert, settings: dict,
                 instrument: str) -> list:
    changed = False
    demo    = settings.get("demo_mode", True)
    for trade in history:
        if trade.get("instrument") != instrument: continue
        if trade.get("status") != "FILLED":       continue
        if trade.get("realized_pnl_usd") is not None: continue
        trade_id = trade.get("trade_id")
        if not trade_id: continue
        pnl = trader.get_trade_pnl(str(trade_id))
        if pnl is None: continue

        trade["realized_pnl_usd"] = pnl
        trade["closed_at_sgt"]    = datetime.now(SGT).strftime("%Y-%m-%d %H:%M:%S")
        changed = True
        log.info("Back-filled P&L %s trade %s: $%.4f", instrument, trade_id, pnl)

        if pnl < 0:
            rt_file = _pair_runtime_file(instrument)
            rt = load_json(rt_file, {})
            rt["last_sl_closed_at_sgt"] = trade["closed_at_sgt"]
            save_json(rt_file, rt)

        if not trade.get("closed_alert_sent"):
            try:
                _cp  = trade.get("tp_price") if pnl > 0 else trade.get("sl_price")
                _dur = ""
                _t1s = trade.get("timestamp_sgt", "")
                _t2s = trade.get("closed_at_sgt", "")
                if _t1s and _t2s:
                    _d = int(
                        (datetime.strptime(_t2s, "%Y-%m-%d %H:%M:%S") -
                         datetime.strptime(_t1s, "%Y-%m-%d %H:%M:%S")).total_seconds() // 60
                    )
                    _dur = f"{_d // 60}h {_d % 60}m" if _d >= 60 else f"{_d}m"
                alert.send(msg_trade_closed(
                    trade_id=trade_id,
                    direction=trade.get("direction", ""),
                    setup=trade.get("setup", ""),
                    entry=float(trade.get("entry", 0)),
                    close_price=float(_cp or 0),
                    pnl=float(pnl),
                    session=trade.get("session", ""),
                    demo=demo,
                    duration_str=_dur,
                    price_dp=_pip_dp(float(trade.get("pip_size", 0.0001) or 0.0001)),
                    max_pips_reached=trade.get("max_pips_reached"),
                ))
                trade["closed_alert_sent"] = True
            except Exception as _e:
                log.warning("Could not send trade_closed alert: %s", _e)

    if changed:
        save_history(history)
    return history


# ── Logging helper ─────────────────────────────────────────────────────────────

def log_event(code: str, message: str, level: str = "info", **extra):
    getattr(log, level, log.info)(f"[{code}] {message}", extra={"event": code, **extra})


# ── Guard phase ────────────────────────────────────────────────────────────────

def _guard_phase(db, run_id, settings, alert, history, now_sgt, today, demo,
                 instrument: str) -> dict | None:
    """All pre-trade guards for one instrument. Returns context dict or None."""

    ops = load_ops_state(instrument)

    for w in run_startup_checks():
        log.warning(w, extra={"run_id": run_id})

    log.info("=== %s | %s | %s SGT ===",
             settings.get("bot_name", "Zen Scalp"), instrument,
             now_sgt.strftime("%Y-%m-%d %H:%M"),
             extra={"run_id": run_id, "pair": instrument})
    update_runtime_state(
        last_cycle_started=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
        last_run_id=run_id, status="RUNNING",
    )

    if not settings.get("enabled", True) or get_bool_env("TRADING_DISABLED", False):
        log.warning("[%s] Trading disabled.", instrument, extra={"run_id": run_id})
        send_once_per_state(alert, ops, "ops_state", "disabled",
                            "⏸️ Trading disabled by configuration.", instrument)
        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                             status="SKIPPED_DISABLED")
        db.finish_cycle(run_id, status="SKIPPED",
                        summary={"stage": "enabled_check", "reason": "disabled"})
        return None

    history[:] = prune_old_trades(history, settings)
    save_history(history)

    weekday = now_sgt.weekday()
    if weekday == 5:
        log.info("Saturday — market closed.", extra={"run_id": run_id})
        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                             status="SKIPPED_MARKET_CLOSED")
        db.finish_cycle(run_id, status="SKIPPED",
                        summary={"stage": "market_guard", "reason": "Saturday"})
        return None
    if weekday == 6:
        log.info("Sunday — waiting for Monday open.", extra={"run_id": run_id})
        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                             status="SKIPPED_MARKET_CLOSED")
        db.finish_cycle(run_id, status="SKIPPED",
                        summary={"stage": "market_guard", "reason": "Sunday"})
        return None
    if weekday == 0 and now_sgt.hour < int(settings.get("trading_day_start_hour_sgt", 8)):
        log.info("Monday pre-open — skipping.", extra={"run_id": run_id})
        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                             status="SKIPPED_MARKET_CLOSED")
        db.finish_cycle(run_id, status="SKIPPED",
                        summary={"stage": "market_guard", "reason": "Monday pre-open"})
        return None

    # ── Dead zone early exit — skip ALL API calls if no open trades ────────
    # 04:00-07:59 SGT: no new entries allowed. If no open trades to manage,
    # skip before any OANDA call. If trades are open, fall through for
    # reconcile + PnL management.
    if is_dead_zone_time(now_sgt, settings):
        _open_in_history = [t for t in history
                            if not t.get("realized_pnl_usd") and t.get("status") != "FAILED"]
        if not _open_in_history:
            log.debug("[%s] Dead zone + no open trades — skipping cycle.",
                      instrument, extra={"run_id": run_id})
            update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                                 status="SKIPPED_DEAD_ZONE")
            db.finish_cycle(run_id, status="SKIPPED",
                            summary={"stage": "dead_zone_early_exit",
                                     "instrument": instrument})
            return None
        log.info("[%s] Dead zone — %d open trade(s) present, management mode only. No new entries.",
                 instrument, len(_open_in_history), extra={"run_id": run_id})

    if settings.get("news_filter_enabled", True):
        try:
            refresh_calendar()
        except Exception as e:
            log.warning("Calendar refresh failed (using cached): %s", e,
                        extra={"run_id": run_id})

    # Loss cooldown check (per-instrument)
    cooldown_started_until, _, cooldown_streak = maybe_start_loss_cooldown(
        history, today, now_sgt, settings, instrument)
    if cooldown_started_until and now_sgt < cooldown_started_until:
        _cd_sess = get_session(now_sgt, settings)[0] or ""
        _, _, _cd_losses = daily_totals(history, today, instrument=instrument)
        send_once_per_state(
            alert, ops, "cooldown_started_state",
            f"cooldown_started:{cooldown_started_until.strftime('%Y-%m-%d %H:%M:%S')}",
            msg_cooldown_started(
                streak=cooldown_streak,
                cooldown_until_sgt=cooldown_started_until.strftime("%H:%M"),
                session_name=_cd_sess,
                day_losses=_cd_losses,
                day_limit=int(settings.get("max_losing_trades_day", 8)),
            ), instrument)
        log_event("COOLDOWN_STARTED",
                  f"[{instrument}] Cooldown until "
                  f"{cooldown_started_until.strftime('%Y-%m-%d %H:%M:%S')} SGT.",
                  run_id=run_id)

    session, macro, threshold = get_session(now_sgt, settings)

    if is_friday_cutoff(now_sgt, settings):
        log_event("FRIDAY_CUTOFF", f"[{instrument}] Friday cutoff.", run_id=run_id)
        send_once_per_state(
            alert, ops, "ops_state",
            f"friday_cutoff:{now_sgt.strftime('%Y-%m-%d')}",
            msg_friday_cutoff(int(settings.get("friday_cutoff_hour_sgt", 23))),
            instrument)
        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                             status="SKIPPED_FRIDAY_CUTOFF")
        db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "friday_cutoff"})
        return None

    if settings.get("session_only", True):
        if session is None:
            if is_dead_zone_time(now_sgt, settings):
                log_event("DEAD_ZONE_SKIP",
                          f"[{instrument}] Dead zone — management active.",
                          run_id=run_id)
            else:
                log.info("[%s] Outside all sessions.", instrument,
                         extra={"run_id": run_id})
            # v1.8: combined "Trading Window Closed" card sent ONCE per
            # session-end transition, globally (not per-pair). Was previously
            # firing twice (once per pair) at every session-end.
            if ops.get("last_session") is not None:
                _last_sess = ops.get("last_session")
                # Determine next session for the card
                _tok_s = int(settings.get("tokyo_session_start_hour", 8))
                _lon_s = int(settings.get("london_session_start_hour", 16))
                if _last_sess and "London" in _last_sess:
                    _next_name, _next_hours = "Tokyo", f"{_tok_s:02d}:00 next day"
                elif _last_sess and "Tokyo" in _last_sess:
                    _next_name, _next_hours = "London", f"{_lon_s:02d}:00"
                else:
                    _next_name, _next_hours = "Tokyo", f"{_tok_s:02d}:00"
                send_once_global(
                    alert,
                    "trading_window_closed",
                    f"window_closed:{_last_sess}:{today}",
                    msg_trading_window_closed(
                        closed_session=_last_sess or "Trading",
                        next_session=_next_name,
                        next_session_hours=_next_hours,
                    ),
                )
                ops["last_session"] = None
                save_ops_state(ops, instrument)
            update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                                 status="SKIPPED_OUTSIDE_SESSION")
            db.finish_cycle(run_id, status="SKIPPED",
                            summary={"stage": "session_check",
                                     "reason": "outside_session"})
            return None
    else:
        if session is None:
            session, macro = "All Hours", "London"
        threshold = int(settings.get("signal_threshold", 4))

    threshold = threshold or int(settings.get("signal_threshold", 4))
    banner    = SESSION_BANNERS.get(macro, "📊") + f" [{_pretty_pair(instrument)}]"
    log.info("[%s] Session: %s (%s)", instrument, session, macro,
             extra={"run_id": run_id})

    if ops.get("last_session") != session:
        if session is not None:
            _lon_s = int(settings.get("london_session_start_hour", 16))
            _lon_e = int(settings.get("london_session_end_hour",   20))
            _us_s  = int(settings.get("us_session_start_hour",     21))
            _us_e2 = int(settings.get("us_session_early_end_hour",  3))
            _tok_s = int(settings.get("tokyo_session_start_hour",   8))
            _tok_e = int(settings.get("tokyo_session_end_hour",    15))
            _hours_map = {
                "US Window":     f"{_us_s:02d}:00–{_us_e2:02d}:59",
                "London Window": f"{_lon_s:02d}:00–{_lon_e:02d}:59",
                "Tokyo Window":  f"{_tok_s:02d}:00–{_tok_e:02d}:59",
            }
            _sess_hours = _hours_map.get(session, "")
            if _sess_hours:
                _dp, _dc, _ = daily_totals(history, today, instrument=instrument)
                _wk   = get_window_key(session)
                _wcap = get_window_trade_cap(_wk, settings) or 0
                # Build combined multi-pair card (EUR_GBP + AUD_USD together)
                _all_pairs     = list(settings.get("pairs", {}).keys())
                _pair_stats    = []
                for _p in _all_pairs:
                    _pd, _dc2, _ = daily_totals(history, today, instrument=_p)
                    _pair_stats.append({
                        "instrument":   _p,
                        "trades_today": _dc2,
                        "daily_pnl":    _pd,
                    })
                _card = msg_session_open_multi(
                    session_name=session,
                    session_hours_sgt=_sess_hours,
                    pairs=_pair_stats,
                    trade_cap=_wcap,
                )
                # v1.7 fix: dedup is GLOBAL across all pairs — the card itself
                # contains data for all pairs, so each pair's cycle reaching this
                # block must not re-send. Was per-pair in v1.6.x, causing the
                # session-open message to fire twice (once per pair) every
                # session start.
                send_once_global(
                    alert,
                    "session_open_state", f"session_open:{session}:{today}",
                    _card,
                )
        ops["last_session"] = session
        ops.pop("ops_state", None)
        save_ops_state(ops, instrument)

    # ── News filter ────────────────────────────────────────────────────────────
    news_penalty = 0
    news_status  = {}
    if settings.get("news_filter_enabled", True):
        nf = NewsFilter(
            before_minutes  =int(settings.get("news_block_before_min",    30)),
            after_minutes   =int(settings.get("news_block_after_min",     30)),
            lookahead_minutes=int(settings.get("news_lookahead_min",      120)),
            medium_penalty  =int(settings.get("news_medium_penalty_score", -1)),
            fail_closed     =bool(settings.get("news_fail_closed", True)),
            max_cache_age_hours=int(settings.get("calendar_cache_max_age_hours", 24)),
        )
        news_status  = nf.get_status_now()
        blocked      = bool(news_status.get("blocked"))
        reason       = str(news_status.get("reason", "No blocking news"))
        news_penalty = int(news_status.get("penalty", 0))
        lookahead    = news_status.get("lookahead", [])
        if lookahead:
            log.info("[%s] Upcoming: %s", instrument,
                     " | ".join(f"{e['name']} in {e['mins_away']}min"
                                for e in lookahead[:3]),
                     extra={"run_id": run_id})
        if blocked:
            _evt = news_status.get("event", {})
            send_once_per_state(alert, ops, "ops_state", f"news:{reason}",
                                msg_news_block(
                                    event_name=_evt.get("name", reason),
                                    event_time_sgt=_evt.get("time_sgt", ""),
                                    before_min=int(settings.get("news_block_before_min", 30)),
                                    after_min =int(settings.get("news_block_after_min",  30)),
                                ), instrument)
            update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                                 status="SKIPPED_NEWS_BLOCK", reason=reason)
            db.finish_cycle(run_id, status="SKIPPED",
                            summary={"stage": "news_filter", "reason": reason})
            return None

    # ── Early cap guards (no OANDA call needed) ────────────────────────────
    _dp_pre, _dt_pre, _dl_pre = daily_totals(history, today, instrument=instrument)

    _max_day_losses = int(settings.get("max_losing_trades_day", 8))
    if _max_day_losses > 0 and _dl_pre >= _max_day_losses:
        msg = (f"🛑 [{_pretty_pair(instrument)}] Daily loss cap ({_dl_pre}/{_max_day_losses}) — "
               f"no new entries today.")
        send_once_per_state(alert, ops, "ops_state",
                            f"day_loss_cap:{today}:{_dl_pre}", msg, instrument)
        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                             status="SKIPPED_DAILY_LOSS_CAP")
        db.finish_cycle(run_id, status="SKIPPED",
                        summary={"stage": "daily_loss_cap", "instrument": instrument,
                                 "losses": _dl_pre, "cap": _max_day_losses})
        return None

    _max_day_trades = int(settings.get("max_trades_day", 20))
    if _max_day_trades > 0 and _dt_pre >= _max_day_trades:
        msg = (f"🛑 [{_pretty_pair(instrument)}] Daily trade cap ({_dt_pre}/{_max_day_trades}) — "
               f"no new entries today.")
        send_once_per_state(alert, ops, "ops_state",
                            f"day_trade_cap:{today}:{_dt_pre}", msg, instrument)
        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                             status="SKIPPED_DAILY_TRADE_CAP")
        db.finish_cycle(run_id, status="SKIPPED",
                        summary={"stage": "daily_trade_cap", "instrument": instrument,
                                 "trades": _dt_pre, "cap": _max_day_trades})
        return None

    _window_key = get_window_key(session)
    _window_cap = get_window_trade_cap(_window_key, settings)
    if _window_key and _window_cap is not None:
        _wt = window_trade_count(history, today, _window_key, instrument)
        if _wt >= _window_cap:
            msg = (f"⏸️ [{_pretty_pair(instrument)}] {session} cap ({_wt}/{_window_cap}) — "
                   f"no more entries this window.")
            send_once_per_state(alert, ops, "window_cap_state",
                                f"window_cap:{_window_key}:{today}:{_wt}",
                                msg, instrument)
            update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                                 status="SKIPPED_WINDOW_CAP")
            db.finish_cycle(run_id, status="SKIPPED",
                            summary={"stage": "window_cap", "instrument": instrument,
                                     "window": _window_key,
                                     "trades": _wt, "cap": _window_cap})
            return None

    if macro:
        _sl_cap  = int(settings.get("max_losing_trades_session", 4))
        _sl_sess = session_losses(history, today, macro, instrument)
        if _sl_cap > 0 and _sl_sess >= _sl_cap:
            msg = (f"🛑 [{_pretty_pair(instrument)}] {session or macro} session loss cap "
                   f"({_sl_sess}/{_sl_cap}) — no more entries.")
            send_once_per_state(alert, ops, "session_loss_cap_state",
                                f"sess_loss_cap:{macro}:{today}:{_sl_sess}",
                                msg, instrument)
            update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                                 status="SKIPPED_SESSION_LOSS_CAP")
            db.finish_cycle(run_id, status="SKIPPED",
                            summary={"stage": "session_loss_cap",
                                     "instrument": instrument,
                                     "session": macro,
                                     "losses": _sl_sess, "cap": _sl_cap})
            return None

    # ── OANDA login ───────────────────────────────────────────────────────────
    trader          = OandaTrader(demo=demo)
    account_summary = trader.login_with_summary()
    _cb             = load_json(RUNTIME_STATE_FILE, {})
    _cb_fail        = int(_cb.get("oanda_consecutive_failures", 0))

    if account_summary is None:
        _cb_fail += 1
        save_json(RUNTIME_STATE_FILE, {**_cb, "oanda_consecutive_failures": _cb_fail})
        if _cb_fail == 1 or _cb_fail % 12 == 0:
            alert.send(msg_error("OANDA login failed",
                                 f"Consecutive failures: {_cb_fail}."))
        log.warning("OANDA login failed (consecutive=%d)", _cb_fail)
        db.finish_cycle(run_id, status="FAILED",
                        summary={"stage": "oanda_login", "reason": "login_failed",
                                 "consecutive_failures": _cb_fail})
        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                             status="FAILED_LOGIN")
        return None

    if _cb_fail > 0:
        save_json(RUNTIME_STATE_FILE, {**_cb, "oanda_consecutive_failures": 0})
        if _cb_fail >= 3:
            alert.send(f"✅ OANDA restored after {_cb_fail} failure(s).")

    balance = account_summary["balance"]
    if balance <= 0:
        alert.send(msg_error("Cannot fetch balance", "OANDA returned $0"))
        db.finish_cycle(run_id, status="FAILED",
                        summary={"stage": "oanda_login", "reason": "invalid_balance"})
        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                             status="FAILED_LOGIN")
        return None

    reconcile = reconcile_runtime_state(trader, history, instrument, now_sgt, alert=alert)
    if reconcile.get("recovered_trade_ids") or reconcile.get("backfilled_trade_ids"):
        save_history(history)
    db.upsert_state(f"last_reconciliation_{instrument}",
                    {**reconcile,
                     "checked_at_sgt": now_sgt.strftime("%Y-%m-%d %H:%M:%S")})

    history[:] = backfill_pnl(history, trader, alert, settings, instrument)
    if settings.get("breakeven_enabled", False):
        check_breakeven(history, trader, alert, settings, instrument)

    # v1.6.1: weekend gap-risk protection — force-close all open positions
    # by Friday 22:00 SGT (configurable). Runs after BE check so any
    # in-flight BE move completes first, then weekend close acts on whatever
    # remains. Independent of `is_friday_cutoff` (which only blocks new entries).
    if is_weekend_close_time(now_sgt, settings):
        force_close_for_weekend(history, trader, alert, settings, instrument, now_sgt)

    # Track peak pip distance on every open trade
    if track_max_pips(history, trader, settings, instrument):
        save_history(history)

    # ── SL re-entry gap ───────────────────────────────────────────────────────
    _sl_gap_min = int(settings.get("sl_reentry_gap_min", 5))
    if _sl_gap_min > 0:
        _rt = load_json(_pair_runtime_file(instrument), {})
        _last_sl_at = _rt.get("last_sl_closed_at_sgt")
        if _last_sl_at:
            _last_sl_dt = _parse_sgt_timestamp(_last_sl_at)
            if _last_sl_dt and (now_sgt - _last_sl_dt).total_seconds() < _sl_gap_min * 60:
                _rem = max(1, int(
                    (_sl_gap_min * 60 - (now_sgt - _last_sl_dt).total_seconds()) // 60))
                send_once_per_state(
                    alert, ops, "sl_reentry_state", f"sl_gap:{_last_sl_at}",
                    f"⏳ [{_pretty_pair(instrument)}] SL cooldown — {_rem} more min.",
                    instrument)
                update_runtime_state(
                    last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                    status="SKIPPED_SL_REENTRY_GAP")
                db.finish_cycle(run_id, status="SKIPPED",
                                summary={"stage": "sl_reentry_gap",
                                         "instrument": instrument})
                return None

    _, _, daily_losses = daily_totals(history, today, trader=trader,
                                      instrument=instrument)

    cooldown_until = active_cooldown_until(now_sgt, instrument)
    if cooldown_until:
        rem = max(1, int((cooldown_until - now_sgt).total_seconds() // 60))
        send_once_per_state(
            alert, ops, "cooldown_guard_state",
            f"cooldown:{cooldown_until.strftime('%Y-%m-%d %H:%M:%S')}",
            f"🧊 [{_pretty_pair(instrument)}] Cooldown — {rem} more min.", instrument)
        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                             status="SKIPPED_COOLDOWN")
        db.finish_cycle(run_id, status="SKIPPED",
                        summary={"stage": "cooldown_guard", "instrument": instrument})
        return None

    open_count     = trader.get_open_trades_count(instrument)
    max_concurrent = int(settings.get("max_concurrent_trades", 1))
    if open_count >= max_concurrent:
        send_once_per_state(
            alert, ops, "open_cap_state",
            f"open_cap:{open_count}:{max_concurrent}",
            f"⏸️ [{_pretty_pair(instrument)}] Max concurrent trades ({open_count}/{max_concurrent}).",
            instrument)
        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                             status="SKIPPED_OPEN_TRADE_CAP")
        db.finish_cycle(run_id, status="SKIPPED",
                        summary={"stage": "open_trade_guard", "instrument": instrument})
        return None

    # ── Global concurrent-trade cap ───────────────────────────────────────────
    max_total = int(settings.get("max_total_open_trades", 0))
    if max_total > 0:
        total_open = len(trader.get_open_trades())   # all instruments, broker truth
        if total_open >= max_total:
            send_once_per_state(
                alert, ops, "global_cap_state",
                f"global_cap:{total_open}:{max_total}",
                f"⏸️ [{_pretty_pair(instrument)}] Global trade cap ({total_open}/{max_total} open across all pairs).",
                instrument)
            update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                                 status="SKIPPED_GLOBAL_TRADE_CAP")
            db.finish_cycle(run_id, status="SKIPPED",
                            summary={"stage": "global_trade_cap", "instrument": instrument,
                                     "total_open": total_open, "cap": max_total})
            return None

    return {
        "trader": trader,
        "balance": balance, "account_summary": account_summary,
        "session": session, "macro": macro, "threshold": threshold,
        "banner": banner, "ops": ops,
        "news_penalty": news_penalty, "news_status": news_status,
        "effective_balance": get_effective_balance(balance, settings),
    }


# ── Signal phase ───────────────────────────────────────────────────────────────

def _signal_phase(db, run_id, settings, alert, trader, history,
                  now_sgt, today, demo, ctx, instrument: str) -> dict | None:

    session         = ctx["session"]
    macro           = ctx["macro"]
    banner          = ctx["banner"]
    ops             = ctx["ops"]
    sig_cache       = load_signal_cache(instrument)
    news_penalty    = ctx["news_penalty"]
    news_status     = ctx["news_status"]
    balance         = ctx["balance"]
    account_summary = ctx["account_summary"]
    pip             = _pip_size(settings)
    dp              = _pip_dp(pip)

    engine = SignalEngine(demo=demo)
    score, direction, details, levels, position_usd = engine.analyze(
        instrument=instrument, settings=settings)

    raw_score        = score
    raw_position_usd = position_usd

    if news_penalty:
        score        = max(score + news_penalty, 0)
        position_usd = score_to_position_usd(score, settings)
        details      = details + f" | ⚠️ News penalty ({news_penalty:+d})"
        _nev = news_status.get("events", [])
        if not _nev and news_status.get("event"):
            _nev = [news_status["event"]]
        send_once_per_state(
            alert, ops, "ops_state", f"news_penalty:{news_penalty}:{today}",
            msg_news_penalty(
                event_names=[e.get("name", "") for e in _nev],
                penalty=news_penalty, score_after=score, score_before=raw_score,
                position_after=position_usd, position_before=raw_position_usd,
            ), instrument)

    # v2.2 — H1 soft-mode penalty.
    # In SOFT mode the H1 trend filter applies a -1 score penalty when the
    # signal is counter-trend (h1_aligned=False AND h1_trend in BULLISH/BEARISH).
    # In STRICT mode (handled later) the trade is blocked entirely.
    # Until v2.2 the soft-mode penalty was documented in README/CONFLUENCE
    # but never implemented in code — H1 was effectively observe-only,
    # leading to 9/9 counter-trend trades during May 4-6 (78% loss rate).
    h1_penalty = 0
    _h1_mode    = settings.get("h1_filter_mode", "soft")
    _h1_enabled = bool(settings.get("h1_filter_enabled", True))
    _h1_trend   = levels.get("h1_trend", "UNKNOWN")
    _h1_aligned = levels.get("h1_aligned", True)
    if (_h1_enabled and _h1_mode == "soft" and not _h1_aligned and
            _h1_trend not in ("UNKNOWN", "DISABLED", "FLAT")):
        h1_penalty = int(settings.get("h1_soft_penalty", -1))
        score        = max(score + h1_penalty, 0)
        position_usd = score_to_position_usd(score, settings)
        details      = details + f" | 📉 H1 counter-trend penalty ({h1_penalty:+d})"

    _tf = settings.get("candle_timeframe", "M15")
    db.record_signal(
        {"pair": instrument, "timeframe": _tf, "side": direction,
         "score": score, "raw_score": raw_score,
         "news_penalty": news_penalty,
         "h1_penalty": h1_penalty,
         "details": details, "levels": levels},
        timeframe=_tf, run_id=run_id,
    )

    cpr_w = levels.get("cpr_width_pct", 0)
    # Resolve per-session threshold BEFORE the closure so all _send_signal_update
    # calls display the correct session threshold (not the global fallback).
    _thr = int(ctx.get("threshold", settings.get("signal_threshold", 4)))

    def _send_signal_update(decision, reason, extra_payload=None):
        payload = _signal_payload(score=score, direction=direction,
                                  signal_threshold=_thr,
                                  min_rr_ratio=float(settings.get("min_rr_ratio", 1.4)),
                                  **(extra_payload or {}))
        msg = msg_signal_update(
            banner=banner, session=session, direction=direction,
            score=score, position_usd=position_usd, cpr_width_pct=cpr_w,
            detail_lines=details.split(" | "), news_penalty=news_penalty,
            raw_score=raw_score, decision=decision, reason=reason,
            cycle_minutes=int(settings.get("cycle_minutes", 5)),
            signal_threshold=_thr,
            setup=levels.get("setup", ""),
            h1_trend=levels.get("h1_trend", "UNKNOWN"),
            h1_aligned=levels.get("h1_aligned", True),
            h1_filter_mode=settings.get("h1_filter_mode", "soft"),
            **payload,
        )
        if msg != sig_cache.get("last_signal_msg", ""):
            alert.send(msg)
            sig_cache.update({"score": score, "direction": direction,
                              "last_signal_msg": msg})
            save_signal_cache(sig_cache, instrument)

    _tg_min_score = int(settings.get("telegram_min_score_alert", 3))
    if direction == "NONE" or position_usd <= 0:
        if score >= _tg_min_score or _tg_min_score == 0:
            _send_signal_update("WATCHING", _clean_reason(details),
                                {"session_ok": True, "news_ok": True,
                                 "open_trade_ok": True})
        update_runtime_state(
            last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
            status="COMPLETED_NO_SIGNAL", score=score, direction=direction)
        db.finish_cycle(run_id, status="COMPLETED",
                        summary={"signals": 1, "trades_placed": 0,
                                 "score": score, "direction": direction,
                                 "instrument": instrument})
        return None

    if score < _thr:
        if score >= _tg_min_score or _tg_min_score == 0:
            _send_signal_update(
                "WATCHING", f"Score {score}/6 below threshold ({_thr})",
                {"session_ok": True, "news_ok": True, "open_trade_ok": True})
        update_runtime_state(
            last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
            status="COMPLETED_BELOW_THRESHOLD", score=score, direction=direction)
        db.finish_cycle(run_id, status="COMPLETED",
                        summary={"signals": 1, "trades_placed": 0,
                                 "score": score, "direction": direction,
                                 "reason": "below_threshold",
                                 "instrument": instrument})
        return None

    # ── Position sizing ───────────────────────────────────────────────────────
    entry = levels.get("entry", 0)
    if entry <= 0:
        _, _, ask = trader.get_price(instrument)
        entry = ask or 0

    # sl_price_dist — raw price distance (e.g. 0.0020 for EUR/GBP 20p).
    # Used for pip counting and OANDA order price levels only.
    # sl_usd / tp_usd — true USD value via pip_value_usd from pair_sl_tp.
    # For GBP-quoted EUR/GBP, sl_price_dist is in GBP; sl_usd_rec converts
    # to USD using pip_value_usd. Always use sl_usd for sizing.
    sl_price_dist = compute_sl_usd(levels, settings)
    tp_price_dist = compute_tp_usd(levels, sl_price_dist, settings)
    sl_usd   = float(levels.get("sl_usd_rec") or sl_price_dist)
    tp_usd   = float(levels.get("tp_usd_rec") or tp_price_dist)
    rr_ratio = derive_rr_ratio(levels, sl_usd, tp_usd, settings)
    units    = calculate_units_from_position(position_usd, sl_usd)
    tp_pct   = (tp_price_dist / entry * 100) if entry > 0 else None

    if units <= 0:
        alert.send(msg_error(f"[{_pretty_pair(instrument)}] Position size = 0",
                             f"position_usd=${position_usd} sl={sl_usd:.6f}"))
        db.finish_cycle(run_id, status="SKIPPED",
                        summary={"stage": "position_sizing", "reason": "zero_units",
                                 "instrument": instrument})
        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                             status="SKIPPED_ZERO_UNITS")
        return None

    signal_blockers = list(levels.get("signal_blockers") or [])

    # Final RR hard block — defense in depth. signals.py normally adds an RR
    # blocker, but this prevents execution if RR is recomputed differently after
    # pair/USD conversion or future code changes.
    min_rr = float(settings.get("min_rr_ratio", 1.4))
    if rr_ratio is None or rr_ratio < min_rr:
        reason = f"RR {rr_ratio:.2f} < min_rr_ratio {min_rr:.2f}" if rr_ratio is not None else "RR unavailable"
        log.warning("[%s] Trade blocked — %s", instrument, reason, extra={"run_id": run_id})
        _send_signal_update(
            "BLOCKED", reason,
            {"rr_ratio": rr_ratio, "tp_pct": tp_pct,
             "session_ok": True, "news_ok": True,
             "open_trade_ok": True, "margin_ok": None})
        update_runtime_state(
            last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
            status="SKIPPED_RR_BLOCK", score=score, direction=direction)
        db.finish_cycle(run_id, status="SKIPPED",
                        summary={"stage": "rr_guard", "reason": reason,
                                 "instrument": instrument, "rr_ratio": rr_ratio,
                                 "min_rr_ratio": min_rr})
        return None

    # H1 strict block — only when h1_filter_mode = "strict"
    _h1_mode    = settings.get("h1_filter_mode", "soft")
    _h1_enabled = bool(settings.get("h1_filter_enabled", True))
    _h1_trend   = levels.get("h1_trend", "UNKNOWN")
    _h1_aligned = levels.get("h1_aligned", True)
    if (_h1_enabled and _h1_mode == "strict" and
            not _h1_aligned and _h1_trend not in ("UNKNOWN", "DISABLED", "FLAT")):
        _h1_dir    = "bullish" if direction == "BUY" else "bearish"
        _h1_reason = f"H1 {_h1_trend} — no {direction} until H1 turns {_h1_dir}"
        _send_signal_update("BLOCKED", _h1_reason)
        update_runtime_state(
            last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
            status="SKIPPED_H1_BLOCK", score=score, direction=direction)
        db.finish_cycle(run_id, status="SKIPPED",
                        summary={"stage": "h1_filter", "reason": _h1_reason,
                                 "instrument": instrument})
        return None

    if signal_blockers:
        _send_signal_update("BLOCKED", signal_blockers[0],
                            {"rr_ratio": rr_ratio, "tp_pct": tp_pct,
                             "session_ok": True, "news_ok": True,
                             "open_trade_ok": True, "margin_ok": None})
        update_runtime_state(
            last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
            status="SKIPPED_SIGNAL_BLOCKED", reason=signal_blockers[0])
        db.finish_cycle(run_id, status="SKIPPED",
                        summary={"stage": "signal_validation",
                                 "reason": signal_blockers[0],
                                 "instrument": instrument})
        return None

    # ── Margin guard ──────────────────────────────────────────────────────────
    margin_available = float(
        account_summary.get("margin_available", balance or 0) or 0)
    price_for_margin = entry or float(levels.get("current_price", entry) or 0)
    units, margin_info = apply_margin_guard(
        trader=trader, instrument=instrument,
        requested_units=units, entry_price=price_for_margin,
        free_margin=margin_available, settings=settings)
    if margin_info.get("status") == "ADJUSTED":
        log.warning("[%s] Margin adjusted %.2f → %.2f | free=%.2f required=%.2f",
                    instrument,
                    float(margin_info.get("requested_units", 0)),
                    float(margin_info.get("final_units",     0)),
                    float(margin_info.get("free_margin",     0)),
                    float(margin_info.get("required_margin", 0)))
        alert.send(msg_margin_adjustment(
            instrument=instrument,
            requested_units=float(margin_info.get("requested_units", 0)),
            adjusted_units =float(margin_info.get("final_units",     0)),
            free_margin    =float(margin_info.get("free_margin",     0)),
            required_margin=float(margin_info.get("required_margin", 0)),
            reason=str(margin_info.get("reason", "margin_guard")),
        ))
    if units <= 0:
        _send_signal_update(
            "BLOCKED", "Insufficient margin",
            {"rr_ratio": rr_ratio, "tp_pct": tp_pct,
             "session_ok": True, "news_ok": True,
             "open_trade_ok": True, "margin_ok": False})
        alert.send(msg_error(
            f"[{_pretty_pair(instrument)}] Insufficient margin",
            f"free=${margin_available:.2f} "
            f"required=${float(margin_info.get('required_margin', 0)):.2f}"))
        db.finish_cycle(run_id, status="SKIPPED",
                        summary={"stage": "margin_cap",
                                 "reason": "insufficient_margin",
                                 "instrument": instrument})
        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                             status="SKIPPED_MARGIN")
        return None

    # Reject margin-adjusted micro-positions that are too small to be meaningful
    _min_units = int(settings.get("min_trade_units", 1000))
    if units < _min_units:
        reason = f"Units {units:.0f} < min_trade_units {_min_units} after margin guard"
        log.warning("[%s] Trade blocked — %s", instrument, reason)
        _send_signal_update(
            "BLOCKED", f"Margin reduced units to {units:.0f} (min {_min_units})",
            {"rr_ratio": rr_ratio, "tp_pct": tp_pct,
             "session_ok": True, "news_ok": True,
             "open_trade_ok": True, "margin_ok": False})
        alert.send(msg_error(
            f"[{_pretty_pair(instrument)}] Units too small after margin guard",
            f"adjusted={units:.0f} min={_min_units} free=${margin_available:.2f}"))
        db.finish_cycle(run_id, status="SKIPPED",
                        summary={"stage": "margin_cap",
                                 "reason": "min_units_not_met",
                                 "instrument": instrument})
        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                             status="SKIPPED_MIN_UNITS")
        return None

    # stop_pips / tp_pips must use this pair's pip_size for the OANDA order
    stop_pips, tp_pips = compute_sl_tp_pips(sl_price_dist, tp_price_dist, pip)
    reward_usd = round(units * tp_usd, 6)

    # ── Spread guard ──────────────────────────────────────────────────────────
    mid, bid, ask = trader.get_price(instrument)
    if mid is None:
        alert.send(msg_error(f"[{_pretty_pair(instrument)}] Cannot fetch price", "OANDA returned None"))
        db.finish_cycle(run_id, status="FAILED",
                        summary={"stage": "pricing", "instrument": instrument})
        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                             status="FAILED_PRICING")
        return None

    spread_pips  = round(abs(ask - bid) / pip)
    spread_limit = int(settings.get("spread_limits", {}).get(
        macro, settings.get("max_spread_pips", 5)))

    if spread_pips > spread_limit:
        _send_signal_update(
            "BLOCKED", f"Spread {spread_pips} > {spread_limit} pips",
            {"rr_ratio": rr_ratio, "tp_pct": tp_pct,
             "spread_pips": spread_pips, "spread_limit": spread_limit,
             "session_ok": True, "news_ok": True,
             "open_trade_ok": True, "margin_ok": True})
        send_once_per_state(
            alert, ops, "spread_state", f"spread:{macro}:{spread_pips}",
            msg_spread_skip(banner, session, spread_pips, spread_limit),
            instrument)
        db.finish_cycle(run_id, status="SKIPPED",
                        summary={"stage": "spread_guard", "instrument": instrument})
        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                             status="SKIPPED_SPREAD_GUARD")
        return None

    _send_signal_update(
        "READY", "All checks passed",
        {"rr_ratio": rr_ratio, "tp_pct": tp_pct,
         "spread_pips": spread_pips, "spread_limit": spread_limit,
         "session_ok": True, "news_ok": True,
         "open_trade_ok": True, "margin_ok": True})

    ctx.update({
        "score": score, "raw_score": raw_score, "direction": direction,
        "details": details, "levels": levels, "position_usd": position_usd,
        "h1_penalty": h1_penalty,
        "entry": entry, "sl_price_dist": sl_price_dist, "tp_price_dist": tp_price_dist,
        "sl_usd": sl_usd, "tp_usd": tp_usd,
        "rr_ratio": rr_ratio, "units": units,
        "stop_pips": stop_pips, "tp_pips": tp_pips,
        "reward_usd": reward_usd, "cpr_w": cpr_w,
        "spread_pips": spread_pips, "bid": bid, "ask": ask,
        "margin_available": margin_available, "price_for_margin": price_for_margin,
        "margin_info": margin_info, "pip": pip, "dp": dp,
    })
    return ctx


# ── Execution phase ────────────────────────────────────────────────────────────

def _execution_phase(db, run_id, settings, alert, trader, history,
                     now_sgt, today, demo, ctx, instrument: str):

    session          = ctx["session"]
    macro            = ctx["macro"]
    banner           = ctx["banner"]
    score            = ctx["score"]
    raw_score        = ctx["raw_score"]
    direction        = ctx["direction"]
    details          = ctx["details"]
    levels           = ctx["levels"]
    position_usd     = ctx["position_usd"]
    entry            = ctx["entry"]
    sl_price_dist    = ctx["sl_price_dist"]
    tp_price_dist    = ctx["tp_price_dist"]
    sl_usd           = ctx["sl_usd"]
    tp_usd           = ctx["tp_usd"]
    rr_ratio         = ctx["rr_ratio"]
    units            = ctx["units"]
    stop_pips        = ctx["stop_pips"]
    tp_pips          = ctx["tp_pips"]
    reward_usd       = ctx["reward_usd"]
    cpr_w            = ctx["cpr_w"]
    spread_pips      = ctx["spread_pips"]
    bid              = ctx["bid"]
    ask              = ctx["ask"]
    margin_available = ctx["margin_available"]
    price_for_margin = ctx["price_for_margin"]
    margin_info      = ctx["margin_info"]
    eff_balance      = ctx["effective_balance"]
    news_penalty     = ctx["news_penalty"]
    h1_penalty       = ctx.get("h1_penalty", 0)
    pip              = ctx["pip"]
    dp               = ctx["dp"]

    # ── Hard dead zone execution block (defense in depth) ──────────────────
    # Even if session logic somehow passes, no new order can be placed
    # during 04:00–07:59 SGT. This is a final hard stop before any OANDA call.
    if is_dead_zone_time(now_sgt, settings):
        log.warning("[%s] Hard dead zone block fired at execution phase (%s SGT) — "
                    "order suppressed. Investigate guard chain.",
                    instrument, now_sgt.strftime("%H:%M"), extra={"run_id": run_id})
        db.finish_cycle(run_id, status="SKIPPED",
                        summary={"stage": "dead_zone_hard_block", "instrument": instrument})
        return

    sl_price, tp_price = compute_sl_tp_prices(entry, direction, sl_price_dist, tp_price_dist, dp)

    record = {
        "timestamp_sgt":        now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
        "mode":                 "DEMO" if demo else "LIVE",
        "instrument":           instrument,
        "direction":            direction,
        "setup":                levels.get("setup", ""),
        "session":              session,
        "window":               get_window_key(session),
        "macro_session":        macro,
        "score":                score,
        "raw_score":            raw_score,
        "news_penalty":         news_penalty,
        "h1_penalty":           h1_penalty,
        "position_usd":         position_usd,
        "entry":                round(entry, dp),
        "sl_price":             sl_price,
        "tp_price":             tp_price,
        "size":                 units,
        "cpr_width_pct":        cpr_w,
        "h1_trend":             levels.get("h1_trend", "UNKNOWN"),
        "h1_aligned":           levels.get("h1_aligned", True),
        "max_pips_reached":     None,
        "breakeven_step":       0,
        "sl_usd":               round(sl_usd, dp + 2),
        "tp_usd":               round(tp_usd, dp + 2),
        "pip_size":             pip,
        # Actual estimated risk/reward after margin scaling. position_usd is the
        # requested risk target, but units may be reduced by margin guard.
        "estimated_risk_usd":   round(units * sl_usd, 6),
        "estimated_reward_usd": round(reward_usd, 6),
        "spread_pips":          spread_pips,
        "stop_pips":            stop_pips,
        "tp_pips":              tp_pips,
        "levels":               levels,
        "details":              details,
        "trade_id":             None,
        "status":               "FAILED",
        "realized_pnl_usd":     None,
    }

    result = trader.place_order(
        instrument=instrument, direction=direction,
        size=units, stop_distance=stop_pips, limit_distance=tp_pips,
        bid=bid, ask=ask,
    )

    if not result.get("success"):
        err             = result.get("error", "Unknown")
        retry_attempted = False
        if settings.get("auto_scale_on_margin_reject", True) and "MARGIN" in str(err).upper():
            retry_attempted  = True
            retry_safety     = float(settings.get("margin_retry_safety_factor", 0.4))
            retry_specs      = trader.get_instrument_specs(instrument)
            retry_override   = float(settings.get("margin_rate_override", 0.0) or 0.0)
            retry_broker     = float(retry_specs.get("marginRate", 0.02) or 0.02)
            retry_margin     = max(retry_broker, retry_override)
            retry_units      = trader.normalize_units(
                instrument,
                (margin_available * retry_safety) / max(
                    price_for_margin * retry_margin, 1e-9))
            if 0 < retry_units < units:
                alert.send(msg_margin_adjustment(
                    instrument=instrument,
                    requested_units=units, adjusted_units=retry_units,
                    free_margin=margin_available,
                    required_margin=trader.estimate_required_margin(
                        instrument, retry_units, price_for_margin),
                    reason="broker_margin_reject_retry",
                ))
                retry_result = trader.place_order(
                    instrument=instrument, direction=direction,
                    size=retry_units, stop_distance=stop_pips,
                    limit_distance=tp_pips, bid=bid, ask=ask,
                )
                if retry_result.get("success"):
                    result = retry_result
                    units  = retry_units
                    record["size"] = units
                    record["estimated_risk_usd"] = round(units * sl_usd, 6)
                    record["estimated_reward_usd"] = round(units * tp_usd, 6)

        if not result.get("success"):
            alert.send(msg_order_failed(
                direction, instrument, units, result.get("error", "Unknown"),
                free_margin=margin_available,
                required_margin=trader.estimate_required_margin(
                    instrument, units, price_for_margin),
                retry_attempted=retry_attempted,
            ))
            log.error("[%s] Order failed: %s", instrument,
                      result.get("error"), extra={"run_id": run_id})

    if result.get("success"):
        record["trade_id"] = result.get("trade_id")
        record["status"]   = "FILLED"
        fill_price = result.get("fill_price")
        if fill_price and fill_price > 0:
            ae                     = fill_price
            record["entry"]        = round(ae, dp)
            record["signal_entry"] = round(entry, dp)
            record["sl_price"]     = round(ae - sl_price_dist if direction == "BUY"
                                           else ae + sl_price_dist, dp)
            record["tp_price"]     = round(ae + tp_price_dist if direction == "BUY"
                                           else ae - tp_price_dist, dp)

        alert.send(msg_trade_opened(
            banner=banner, direction=direction, setup=levels.get("setup", ""),
            session=session, fill_price=record["entry"], signal_price=entry,
            sl_price=record["sl_price"], tp_price=record["tp_price"],
            sl_usd=sl_usd, tp_usd=tp_usd, units=units, position_usd=position_usd,
            rr_ratio=rr_ratio, cpr_width_pct=cpr_w, spread_pips=spread_pips,
            score=score, balance=eff_balance, demo=demo,
            news_penalty=news_penalty, raw_score=raw_score,
            free_margin=margin_info.get("free_margin"),
            required_margin=trader.estimate_required_margin(
                instrument, units, price_for_margin),
            margin_mode=(
                "RETRIED"
                if record["size"] != float(margin_info.get("final_units", record["size"]))
                else margin_info.get("status", "NORMAL")),
            margin_usage_pct=(
                (trader.estimate_required_margin(instrument, units, price_for_margin) /
                 float(margin_info.get("free_margin", 0)) * 100)
                if float(margin_info.get("free_margin", 0)) > 0 else None),
            price_dp=dp,
            tp2_rr=float(settings.get("tp2_rr_reference", 3.0)),
        ))
        log.info("[%s] Trade placed: %s", instrument, record,
                 extra={"run_id": run_id})

    history.append(record)
    save_history(history)
    db.record_trade_attempt(
        {"pair": instrument, "timeframe": settings.get("candle_timeframe", "M15"),
         "side": direction, "score": score, **record},
        ok=bool(result.get("success")),
        note=result.get("error", "trade placed"),
        broker_trade_id=record.get("trade_id"), run_id=run_id,
    )
    db.upsert_state(f"last_trade_attempt_{instrument}", {
        "run_id": run_id, "success": bool(result.get("success")),
        "trade_id": record.get("trade_id"),
        "timestamp_sgt": record["timestamp_sgt"],
        "instrument": instrument,
    })
    update_runtime_state(
        last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
        status="COMPLETED", score=score, direction=direction,
        trade_status=record["status"],
    )
    db.finish_cycle(run_id, status="COMPLETED", summary={
        "signals": 1,
        "trades_placed": int(bool(result.get("success"))),
        "score": score, "direction": direction,
        "trade_status": record["status"],
        "instrument": instrument,
    })


# ── Main cycle ─────────────────────────────────────────────────────────────────

def run_bot_cycle(alert: "TelegramAlert | None" = None):
    """Orchestrator — runs guard → signal → execution for every enabled pair.

    alert — optional pre-constructed TelegramAlert injected by scheduler.
    """
    global _startup_reconcile_done

    settings      = validate_settings(load_settings())
    db            = Database()
    demo          = settings.get("demo_mode", True)
    alert         = alert or TelegramAlert()
    history       = load_history()
    now_sgt       = datetime.now(SGT)
    _day_start    = int(settings.get("trading_day_start_hour_sgt", 8))
    today         = get_trading_day(now_sgt, _day_start)
    enabled_pairs = get_enabled_pairs(settings)

    if not enabled_pairs:
        log.warning("No pairs enabled in settings['pairs'] — nothing to trade.")
        return

    # ── Startup OANDA reconcile (once per process) ────────────────────────────
    if not _startup_reconcile_done:
        _startup_reconcile_done = True
        try:
            _rt = OandaTrader(demo=demo)
            for instr, _ in enabled_pairs:
                recon = startup_oanda_reconcile(_rt, history, instr, today, now_sgt)
                if recon["injected"] or recon["backfilled"]:
                    save_history(history)
                    log.info("[%s] Startup reconcile: injected=%s backfilled=%s",
                             instr, recon["injected"], recon["backfilled"])
                    if recon["injected"]:
                        alert.send(
                            f"♻️ [{instr}] Reconcile injected "
                            f"{len(recon['injected'])} trade(s): "
                            f"{', '.join(recon['injected'])}"
                        )
        except Exception as _e:
            log.warning("Startup reconcile failed (non-fatal): %s", _e)

    # ── Per-pair cycle ─────────────────────────────────────────────────────────
    for instrument, pair_cfg in enabled_pairs:
        # Merge: pair_cfg overrides global settings for this pair's cycle
        eff = get_effective_settings(settings, pair_cfg)

        with db.cycle() as run_id:
            try:
                ctx = _guard_phase(
                    db, run_id, eff, alert, history,
                    now_sgt, today, demo, instrument)
                if ctx is None:
                    continue

                ctx = _signal_phase(
                    db, run_id, eff, alert, ctx["trader"],
                    history, now_sgt, today, demo, ctx, instrument)
                if ctx is None:
                    continue

                _execution_phase(
                    db, run_id, eff, alert, ctx["trader"],
                    history, now_sgt, today, demo, ctx, instrument)

            except Exception as exc:
                update_runtime_state(
                    last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                    status="FAILED", error=str(exc))
                log.exception("[%s] Unhandled error in cycle: %s", instrument, exc)
                raise


def main():
    return run_bot_cycle()


if __name__ == "__main__":
    main()
