"""Signal engine for Bollinger Band + RSI mean reversion — Zen Scalp v1.0

Strategy: Mean Reversion using Bollinger Bands (20, 2σ) + RSI (14)
Pairs:     EUR_GBP, AUD_USD
Timeframe: M15 candles (5-min cycle)

Scoring (0–6):
  BB extreme   — price beyond outer Bollinger Band (2σ):      +3
               — price within 10% of outer band (approaching): +1
  RSI extreme  — RSI > 70 (overbought) or < 30 (oversold):    +2
               — RSI > 80 or < 20 (very extreme):              +1 bonus
  CPR bias     — price confirms reversion direction vs CPR:    +1

Direction:
  SELL — price > upper BB + RSI overbought  (expect reversion down to mean)
  BUY  — price < lower BB + RSI oversold    (expect reversion up to mean)

TP: ~middle band (SMA20) = ~30 pips
SL: just outside outer band = ~20 pips
RR: 1.5× | Break-even WR: 40%
"""

import json
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path

import pytz

from config_loader import DATA_DIR, get_bool_env
from oanda_trader import OandaTrader

log = logging.getLogger(__name__)

SGT = pytz.timezone("Asia/Singapore")

_CPR_CACHE_FILE  = DATA_DIR / "cpr_cache.json"
_BB_CACHE_FILE   = DATA_DIR / "bb_cache.json"

# ── Defaults ──────────────────────────────────────────────────────────────────
BB_PERIOD     = 20
BB_STD_DEV    = 2.0
RSI_PERIOD    = 14
RSI_OB        = 70
RSI_OS        = 30
CANDLE_TF     = "M15"
CANDLE_COUNT  = 60   # fetch 60 M15 candles (~15 hours)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _price_dp(pip_size: float) -> int:
    return max(0, round(-math.log10(pip_size)))


def score_to_position_usd(score: int, settings: dict | None = None) -> int:
    s = settings or {}
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
        p  = float(levels["pivot"])
        tc = float(levels["tc"])
        bc = float(levels["bc"])
        if p > 0 and tc > 0 and bc > 0:
            return p, tc, bc
    except Exception:
        pass
    return None, None, None


def _ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    k   = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _sma(values: list[float], period: int) -> list[float]:
    return [sum(values[i:i+period]) / period for i in range(len(values) - period + 1)]


def _bollinger(closes: list[float], period: int = BB_PERIOD,
               std_mult: float = BB_STD_DEV) -> tuple:
    """Returns (upper, middle/sma, lower) for last candle."""
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid    = sum(window) / period
    std    = math.sqrt(sum((x - mid) ** 2 for x in window) / period)
    return mid + std_mult * std, mid, mid - std_mult * std


def _rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
    gains  = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    avg_g  = sum(gains[:period]) / period
    avg_l  = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - (100 / (1 + rs)), 2)


# ── Main Signal Engine ────────────────────────────────────────────────────────

class SignalEngine:

    def __init__(self, demo: bool = True):
        self.trader = OandaTrader(demo=demo)

    def analyze(self, instrument: str = "EUR_GBP", settings: dict | None = None):
        """Run Bollinger Band + RSI mean reversion scoring engine.

        Returns:
            score (int), direction (str), setup_name (str),
            cpr_levels (dict), position_usd (int)
        """
        s            = settings or {}
        bb_period    = int(s.get("bb_period",         BB_PERIOD))
        bb_std       = float(s.get("bb_std_dev",      BB_STD_DEV))
        rsi_period   = int(s.get("rsi_period",        RSI_PERIOD))
        rsi_ob       = float(s.get("rsi_overbought",  RSI_OB))
        rsi_os       = float(s.get("rsi_oversold",    RSI_OS))
        candle_tf    = s.get("candle_timeframe",       CANDLE_TF)
        candle_count = CANDLE_COUNT

        # ── 1. CPR levels (bias filter) ───────────────────────────────────────
        try:
            cpr_levels = self.trader.get_cpr_levels(instrument)
        except Exception as e:
            log.warning("[%s] CPR fetch failed: %s", instrument, e)
            cpr_levels = {}

        pivot, tc, bc = _validate_cpr_levels(cpr_levels)

        # ── 2. M15 candles ────────────────────────────────────────────────────
        try:
            candles = self.trader.get_candles(
                instrument, granularity=candle_tf, count=candle_count)
        except Exception as e:
            log.warning("[%s] Candle fetch failed: %s", instrument, e)
            return 0, "NONE", "Could not fetch candles", cpr_levels, 0

        if not candles or len(candles) < bb_period + rsi_period + 5:
            return 0, "NONE", "Not enough candle data", cpr_levels, 0

        closes = [float(c["mid"]["c"]) for c in candles if c.get("complete", True)]
        if len(closes) < bb_period + rsi_period + 5:
            return 0, "NONE", "Not enough complete candles", cpr_levels, 0

        current_price = closes[-1]
        pair_cfg      = (s.get("pair_sl_tp") or {}).get(instrument, {})
        pip_size      = float(pair_cfg.get("pip_size", 0.0001))

        # ── 3. Bollinger Bands ────────────────────────────────────────────────
        upper, mid, lower = _bollinger(closes, bb_period, bb_std)
        if upper is None:
            return 0, "NONE", "BB calculation failed", cpr_levels, 0

        band_width = upper - lower

        # ── 4. RSI ────────────────────────────────────────────────────────────
        rsi_val = _rsi(closes, rsi_period)
        if rsi_val is None:
            return 0, "NONE", "RSI calculation failed", cpr_levels, 0

        # ── 5. Determine reversion direction ─────────────────────────────────
        # SELL: price at/above upper band + RSI overbought
        # BUY:  price at/below lower band + RSI oversold
        at_upper = current_price >= upper
        at_lower = current_price <= lower
        near_upper = (upper - current_price) <= band_width * 0.10
        near_lower = (current_price - lower) <= band_width * 0.10
        rsi_overbought = rsi_val >= rsi_ob
        rsi_oversold   = rsi_val <= rsi_os
        rsi_very_ob    = rsi_val >= 80
        rsi_very_os    = rsi_val <= 20

        # Determine direction
        if at_upper or (near_upper and rsi_overbought):
            direction = "SELL"
        elif at_lower or (near_lower and rsi_oversold):
            direction = "BUY"
        else:
            return 0, "NONE", "Price not at BB extreme", cpr_levels, 0

        # ── 6. Score ──────────────────────────────────────────────────────────
        score       = 0
        score_parts = []

        # BB extreme (+3) or approaching (+1)
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

        # RSI confirmation (+2 standard, +1 very extreme bonus)
        if direction == "SELL" and rsi_overbought:
            score += 2; score_parts.append(f"RSI OB {rsi_val:.1f} +2")
            if rsi_very_ob:
                score += 1; score_parts.append("RSI very OB +1")
        elif direction == "BUY" and rsi_oversold:
            score += 2; score_parts.append(f"RSI OS {rsi_val:.1f} +2")
            if rsi_very_os:
                score += 1; score_parts.append("RSI very OS +1")

        # CPR bias (+1) — price beyond CPR in direction that supports reversion
        if pivot is not None:
            if direction == "SELL" and current_price > pivot:
                score += 1; score_parts.append("CPR above pivot +1")
            elif direction == "BUY" and current_price < pivot:
                score += 1; score_parts.append("CPR below pivot +1")

        score     = min(score, 6)
        setup_name = f"BB+RSI {'SELL' if direction=='SELL' else 'BUY'} Reversion"
        thr        = int(s.get("signal_threshold", 4))

        log.info(
            "Signal | %s setup=%s dir=%s score=%d/6 rsi=%.1f "
            "bb_upper=%.5f bb_mid=%.5f bb_lower=%.5f price=%.5f",
            instrument, setup_name, direction, score, rsi_val,
            upper, mid, lower, current_price
        )

        # Extra context for Telegram watching card
        extra = {
            "bb_upper":    round(upper, 5),
            "bb_mid":      round(mid, 5),
            "bb_lower":    round(lower, 5),
            "rsi":         round(rsi_val, 1),
            "band_width_pips": round(band_width / pip_size, 1),
            "score_parts": score_parts,
        }

        if score < thr:
            return score, direction, f"Score {score}/6 below threshold ({thr})", cpr_levels, 0

        pos_usd = score_to_position_usd(score, s)
        return score, direction, setup_name, cpr_levels, pos_usd
