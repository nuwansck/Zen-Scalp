"""Signal engine for Bollinger Band + RSI mean reversion — Zen Scalp v1.7.3

Strategy: Mean Reversion using Bollinger Bands (20, 2σ) + RSI (14)
Pairs:     EUR_GBP, AUD_USD
Timeframe: M15 candles (5-min cycle)

Scoring (0–6):
  BB extreme   — price beyond outer Bollinger Band (2σ):       +3
               — price within 10% of outer band (approaching): +1
  RSI extreme  — RSI > 70 (overbought) or < 30 (oversold):     +2
               — RSI > 80 or < 20 (very extreme bonus):         +1
  CPR bias     — price confirms reversion direction vs pivot:   +1

Direction:
  SELL — price ≥ upper BB + RSI overbought  → reversion down to mean
  BUY  — price ≤ lower BB + RSI oversold    → reversion up to mean

Per-pair targets (v1.7+):
  EUR/GBP: TP 30p · SL 20p · RR 1.5×
  AUD/USD: TP 22p · SL 15p · RR 1.47×

Two-step trailing breakeven on both pairs (see check_breakeven in bot.py).
"""

import logging
import math
import time

import pytz

from config_loader import DATA_DIR, load_secrets
from oanda_trader import make_oanda_session

log = logging.getLogger(__name__)

SGT  = pytz.timezone("Asia/Singapore")
_UTC = pytz.utc

_CPR_CACHE_FILE = DATA_DIR / "cpr_cache.json"

# ── Defaults ──────────────────────────────────────────────────────────────────
BB_PERIOD     = 20
BB_STD_DEV    = 2.0
RSI_PERIOD    = 14
RSI_OB        = 70
RSI_OS        = 30
CANDLE_TF     = "M15"
CANDLE_COUNT  = 60


# ── Standalone helpers ────────────────────────────────────────────────────────

def _price_dp(pip_size: float) -> int:
    return max(0, round(-math.log10(pip_size)))


def score_to_position_usd(score: int, settings: dict | None = None) -> int:
    s       = settings or {}
    full    = int(s.get("position_full_usd",    60))
    partial = int(s.get("position_partial_usd", 45))
    thr     = int(s.get("signal_threshold",      4))
    if score >= thr + 1:
        return full
    elif score >= thr:
        return partial
    return 0


def _validate_cpr_levels(levels: dict) -> tuple:
    try:
        p  = float(levels.get("pivot", 0))
        tc = float(levels.get("tc",    0))
        bc = float(levels.get("bc",    0))
        if p > 0 and tc > 0 and bc > 0:
            return True, "ok"
        return False, "zero values"
    except Exception as e:
        return False, str(e)


def _bollinger(closes: list, period: int = BB_PERIOD,
               std_mult: float = BB_STD_DEV) -> tuple:
    """Return (upper, middle/sma, lower) for the last candle."""
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid    = sum(window) / period
    std    = math.sqrt(sum((x - mid) ** 2 for x in window) / period)
    return mid + std_mult * std, mid, mid - std_mult * std


def _rsi(closes: list, period: int = RSI_PERIOD) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i+1] - closes[i] for i in range(len(closes) - 1)]
    gains  = [max(d, 0)   for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    avg_g  = sum(gains[:period])  / period
    avg_l  = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + gains[i])  / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_g / avg_l)), 2)


def _ema_series(closes: list, period: int) -> list:
    if len(closes) < period:
        return []
    k   = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    out = [ema]
    for p in closes[period:]:
        ema = p * k + ema * (1 - k)
        out.append(ema)
    return out


# ── Signal Engine ─────────────────────────────────────────────────────────────

class SignalEngine:

    def __init__(self, demo: bool = True):
        secrets         = load_secrets()
        self.api_key    = secrets.get("OANDA_API_KEY",    "")
        self.account_id = secrets.get("OANDA_ACCOUNT_ID", "")
        self.base_url   = (
            "https://api-fxpractice.oanda.com" if demo
            else "https://api-fxtrade.oanda.com"
        )
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
        }
        self.session = make_oanda_session(allowed_methods=["GET"])

    # ── Public: analyze ───────────────────────────────────────────────────────

    def analyze(self, instrument: str = "EUR_GBP", settings: dict | None = None):
        """Run BB + RSI mean reversion scoring engine.

        Returns:
            score (int), direction (str), setup_name (str),
            cpr_levels (dict), position_usd (int)
        """
        s            = settings or {}
        bb_period    = int(s.get("bb_period",        BB_PERIOD))
        bb_std       = float(s.get("bb_std_dev",     BB_STD_DEV))
        rsi_period   = int(s.get("rsi_period",       RSI_PERIOD))
        rsi_ob       = float(s.get("rsi_overbought", RSI_OB))
        rsi_os       = float(s.get("rsi_oversold",   RSI_OS))
        candle_tf    = s.get("candle_timeframe",      CANDLE_TF)
        pair_cfg     = (s.get("pair_sl_tp") or {}).get(instrument, {})
        pip_size     = float(pair_cfg.get("pip_size", 0.0001))
        dp           = _price_dp(pip_size)

        # ── 1. CPR levels (bias filter) ───────────────────────────────────────
        cpr_levels, pivot, tc, bc, _ = self._get_cpr_levels(instrument, dp)
        if cpr_levels is None:
            cpr_levels = {}

        # ── 2. M15 candles ────────────────────────────────────────────────────
        closes, highs, lows = self._fetch_candles(instrument, candle_tf, CANDLE_COUNT)

        min_needed = bb_period + rsi_period + 5
        if len(closes) < min_needed:
            return 0, "NONE", f"Not enough {candle_tf} data (need {min_needed})", cpr_levels, 0

        current_price = closes[-1]

        # ── 3. Bollinger Bands ────────────────────────────────────────────────
        upper, mid, lower = _bollinger(closes, bb_period, bb_std)
        if upper is None:
            return 0, "NONE", "BB calculation failed", cpr_levels, 0

        band_width = upper - lower

        # ── 4. RSI ────────────────────────────────────────────────────────────
        rsi_val = _rsi(closes, rsi_period)
        if rsi_val is None:
            return 0, "NONE", "RSI calculation failed", cpr_levels, 0

        # ── 5. H1 trend (soft label only) ─────────────────────────────────────
        h1_period = int(s.get("h1_ema_period", 21))
        h1_info   = self._get_h1_trend(instrument, h1_period, dp)

        # ── 6. Direction ──────────────────────────────────────────────────────
        at_upper   = current_price >= upper
        at_lower   = current_price <= lower
        near_upper = (upper - current_price) <= band_width * 0.10
        near_lower = (current_price - lower) <= band_width * 0.10
        rsi_ob_hit = rsi_val >= rsi_ob
        rsi_os_hit = rsi_val <= rsi_os
        rsi_very_ob = rsi_val >= 80
        rsi_very_os = rsi_val <= 20

        if at_upper or (near_upper and rsi_ob_hit):
            direction = "SELL"
        elif at_lower or (near_lower and rsi_os_hit):
            direction = "BUY"
        else:
            return 0, "NONE", "Price not at BB extreme", cpr_levels, 0

        # ── 7. Score ──────────────────────────────────────────────────────────
        score       = 0
        score_parts = []

        # BB (up to +3)
        if direction == "SELL":
            if at_upper:
                score += 3; score_parts.append("BB upper breach +3")
            elif near_upper:
                score += 1; score_parts.append("BB near upper +1")
        else:
            if at_lower:
                score += 3; score_parts.append("BB lower breach +3")
            elif near_lower:
                score += 1; score_parts.append("BB near lower +1")

        # RSI (up to +3)
        if direction == "SELL" and rsi_ob_hit:
            score += 2; score_parts.append(f"RSI OB {rsi_val:.1f} +2")
            if rsi_very_ob:
                score += 1; score_parts.append("RSI very OB +1")
        elif direction == "BUY" and rsi_os_hit:
            score += 2; score_parts.append(f"RSI OS {rsi_val:.1f} +2")
            if rsi_very_os:
                score += 1; score_parts.append("RSI very OS +1")

        # CPR bias (+1)
        if pivot is not None:
            if direction == "SELL" and current_price > pivot:
                score += 1; score_parts.append("CPR above pivot +1")
            elif direction == "BUY" and current_price < pivot:
                score += 1; score_parts.append("CPR below pivot +1")

        score      = min(score, 6)
        setup_name = f"BB+RSI {'SELL' if direction == 'SELL' else 'BUY'} Reversion"
        thr        = int(s.get("signal_threshold", 4))

        log.info(
            "Signal | %s setup=%s dir=%s score=%d/6 rsi=%.1f "
            "bb_upper=%.5f bb_mid=%.5f bb_lower=%.5f price=%.5f",
            instrument, setup_name, direction, score,
            rsi_val, upper, mid, lower, current_price,
        )

        # ── 8. Inject SL/TP/RR/H1 into levels dict ───────────────────────────
        # bot.py's compute_sl_usd / compute_tp_usd look for sl_price_dist and
        # tp_price_dist in the levels dict. We must inject them here.
        _sl_pips     = int(pair_cfg.get("sl_pips", 20))
        _tp_pips     = int(pair_cfg.get("tp_pips", 30))
        _pip_val_usd = float(pair_cfg.get("pip_value_usd", 10.0))
        _pip_usd_unit = _pip_val_usd / 100_000   # $ per unit per pip

        sl_price_dist = round(_sl_pips * pip_size, dp + 2)
        tp_price_dist = round(_tp_pips * pip_size, dp + 2)
        sl_usd_rec    = round(_sl_pips * _pip_usd_unit, dp + 2)
        tp_usd_rec    = round(_tp_pips * _pip_usd_unit, dp + 2)
        rr_ratio      = round(tp_usd_rec / sl_usd_rec, 2) if sl_usd_rec > 0 else 0

        # H1 trend label (soft mode)
        _h1_period  = int(s.get("h1_ema_period", 21))
        _h1_enabled = bool(s.get("h1_filter_enabled", True))
        if _h1_enabled:
            h1_info = self._get_h1_trend(instrument, _h1_period, dp)
        else:
            h1_info = {"h1_trend": "DISABLED", "h1_ema_now": None, "h1_price": None}

        _h1_aligned = (
            (h1_info["h1_trend"] == "BEARISH" and direction == "SELL") or
            (h1_info["h1_trend"] == "BULLISH" and direction == "BUY")  or
            h1_info["h1_trend"] in ("UNKNOWN", "DISABLED", "FLAT")
        )

        # Inject into levels
        if cpr_levels is None:
            cpr_levels = {}
        cpr_levels["sl_price_dist"] = sl_price_dist
        cpr_levels["tp_price_dist"] = tp_price_dist
        cpr_levels["sl_usd_rec"]    = sl_usd_rec
        cpr_levels["tp_usd_rec"]    = tp_usd_rec
        cpr_levels["rr_ratio"]      = rr_ratio
        cpr_levels["sl_pips"]       = _sl_pips
        cpr_levels["tp_pips"]       = _tp_pips
        cpr_levels["pip_size"]      = pip_size
        cpr_levels["entry"]         = round(current_price, dp)
        cpr_levels["setup"]         = setup_name
        cpr_levels["score"]         = score
        cpr_levels["position_usd"]  = score_to_position_usd(score, s)
        cpr_levels["h1_trend"]      = h1_info["h1_trend"]
        cpr_levels["h1_ema_now"]    = h1_info.get("h1_ema_now")
        cpr_levels["h1_aligned"]    = _h1_aligned
        cpr_levels["mandatory_checks"] = {
            "score_ok": score >= thr,
            "rr_ok":    rr_ratio >= float(s.get("min_rr_ratio", 1.4)),
        }
        cpr_levels["signal_blockers"] = []
        cpr_levels["bb_upper"]   = round(upper,         dp + 1)
        cpr_levels["bb_mid"]     = round(mid,           dp + 1)
        cpr_levels["bb_lower"]   = round(lower,         dp + 1)
        cpr_levels["rsi"]        = round(rsi_val,       1)

        if score < thr:
            return score, direction, f"Score {score}/6 below threshold ({thr})", cpr_levels, 0

        pos_usd = score_to_position_usd(score, s)
        return score, direction, setup_name, cpr_levels, pos_usd

    # ── Private helpers ───────────────────────────────────────────────────────

    def _fetch_candles(self, instrument: str, granularity: str,
                       count: int = 60) -> tuple:
        """Fetch OANDA candles. Returns (closes, highs, lows)."""
        url    = f"{self.base_url}/v3/instruments/{instrument}/candles"
        params = {"count": str(count), "granularity": granularity, "price": "M"}
        for attempt in range(3):
            try:
                r = self.session.get(url, headers=self.headers,
                                     params=params, timeout=15)
                if r.status_code == 200:
                    candles  = r.json().get("candles", [])
                    complete = [c for c in candles if c.get("complete")]
                    return (
                        [float(c["mid"]["c"]) for c in complete],
                        [float(c["mid"]["h"]) for c in complete],
                        [float(c["mid"]["l"]) for c in complete],
                    )
                log.warning("Fetch candles %s %s: HTTP %s",
                            instrument, granularity, r.status_code)
            except Exception as e:
                log.warning("Fetch candles error (%s %s) attempt %s: %s",
                            instrument, granularity, attempt + 1, e)
            time.sleep(1)
        return [], [], []

    def _get_cpr_levels(self, instrument: str, dp: int = 5):
        """Calculate CPR from previous day's OHLC."""
        closes, highs, lows = self._fetch_candles(instrument, "D", 3)
        if len(closes) < 2:
            return None, None, None, None, None

        ph = highs[-2]; pl = lows[-2]; pc = closes[-2]
        pivot = (ph + pl + pc) / 3
        bc    = (ph + pl) / 2
        tc    = (pivot - bc) + pivot
        if tc < bc:
            tc, bc = bc, tc
        dr    = ph - pl

        lv = {
            "pivot":          round(pivot, dp),
            "tc":             round(tc,    dp),
            "bc":             round(bc,    dp),
            "r1":             round((2 * pivot) - pl,  dp),
            "r2":             round(pivot + dr,         dp),
            "s1":             round((2 * pivot) - ph,  dp),
            "s2":             round(pivot - dr,         dp),
            "pdh":            round(ph,   dp),
            "pdl":            round(pl,   dp),
            "cpr_width_pct":  round(abs(tc - bc) / pivot * 100, 3),
        }
        ok, reason = _validate_cpr_levels(lv)
        if not ok:
            log.warning("CPR validation failed — %s | %s", instrument, reason)
            return None, None, None, None, None

        log.info("CPR fetched | %s pivot=%.*f TC=%.*f BC=%.*f width=%.3f%%",
                 instrument, dp, pivot, dp, tc, dp, bc, lv["cpr_width_pct"])
        return lv, lv["pivot"], lv["tc"], lv["bc"], lv["cpr_width_pct"]

    def _get_h1_trend(self, instrument: str, ema_period: int = 21,
                      dp: int = 5) -> dict:
        """Fetch 40 H1 candles and compute EMA21 trend (soft label)."""
        try:
            closes, _, _ = self._fetch_candles(instrument, "H1", 40)
            if len(closes) < ema_period + 2:
                return {"h1_trend": "UNKNOWN", "h1_ema_now": None, "h1_price": None}
            ema_now   = _ema_series(closes[:-1], ema_period)[-1]
            price_now = closes[-1]
            trend = "BULLISH" if price_now > ema_now else (
                    "BEARISH" if price_now < ema_now else "FLAT")
            return {
                "h1_trend":   trend,
                "h1_ema_now": round(ema_now,   dp),
                "h1_price":   round(price_now, dp),
            }
        except Exception as exc:
            log.warning("H1 trend fetch failed (%s): %s", instrument, exc)
            return {"h1_trend": "UNKNOWN", "h1_ema_now": None, "h1_price": None}
