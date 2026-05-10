import json
import logging
from datetime import datetime, timedelta

import pytz

from state_utils import CALENDAR_CACHE_FILE

log = logging.getLogger(__name__)

# Default penalty applied to signal score when a medium-impact news event
# is active. Negative value intentionally reduces score.
# Configurable via settings.json: "news_medium_penalty_score": -1
DEFAULT_MEDIUM_PENALTY = -1


class NewsFilter:
    """Classify USD/GBP events and decide hard block vs soft penalty.

    V2.0 logic:
    - Major events: hard block within configured window
    - Medium events: no hard block, apply soft score penalty when nearby
    - Minor/irrelevant events: ignore

    The medium penalty score is now configurable via settings.json
    (key: news_medium_penalty_score, default: -1).
    """

    MAJOR_KEYWORDS = [
        "fomc", "non-farm", "nfp", "powell", "rate decision",
        "fed chair", "federal reserve",
    ]
    MEDIUM_KEYWORDS = [
        "cpi", "core cpi", "pce", "core pce", "unemployment",
        "jobless claims",
    ]

    def __init__(self, before_minutes: int = 30, after_minutes: int = 30,
                 lookahead_minutes: int = 120, medium_penalty: int = DEFAULT_MEDIUM_PENALTY,
                 fail_closed: bool = True, max_cache_age_hours: int = 24):
        self.before_minutes    = before_minutes
        self.after_minutes     = after_minutes
        self.lookahead_minutes = lookahead_minutes
        self.medium_penalty    = int(medium_penalty)
        self.fail_closed       = bool(fail_closed)
        self.max_cache_age_hours = int(max_cache_age_hours)
        self.sg_tz = pytz.timezone("Asia/Singapore")
        self.path = CALENDAR_CACHE_FILE

    def _calendar_unavailable_status(self, reason: str) -> dict:
        return {
            "blocked": self.fail_closed,
            "penalty": 0,
            "reason": reason,
            "severity": "calendar_unavailable",
            "lookahead": [],
        }

    def classify_event(self, event: dict) -> str | None:
        name     = str(event.get("name", "")).lower()
        currency = str(event.get("currency", "")).upper()
        impact   = str(event.get("impact", "")).lower()

        if currency != "USD":
            return None
        # Accept all impact values that calendar_fetcher passes through.
        # FF feed now returns "high" / "medium" (lowercased on storage).
        # Legacy values ("3", "red", "medium-high") kept for cache compatibility.
        if impact not in {"high", "medium", "3", "red", "medium-high"}:
            return None

        if any(k in name for k in self.MAJOR_KEYWORDS):
            return "major"
        if any(k in name for k in self.MEDIUM_KEYWORDS):
            return "medium"
        return None

    def get_status_now(self) -> dict:
        now = datetime.now(self.sg_tz)

        if not self.path.exists():
            return self._calendar_unavailable_status("calendar_cache.json missing — news protection unavailable")

        try:
            cache_mtime = datetime.fromtimestamp(self.path.stat().st_mtime, tz=self.sg_tz)
            cache_age_hours = (now - cache_mtime).total_seconds() / 3600
            if self.max_cache_age_hours > 0 and cache_age_hours > self.max_cache_age_hours:
                return self._calendar_unavailable_status(
                    f"calendar_cache.json stale ({cache_age_hours:.1f}h old, max {self.max_cache_age_hours}h)"
                )

            with open(self.path, "r", encoding="utf-8") as f:
                events = json.load(f)
            if not isinstance(events, list):
                return self._calendar_unavailable_status("calendar_cache.json invalid — expected list of events")
        except Exception as e:
            log.warning("Could not read calendar_cache.json (%s) — fail-closed=%s.", e, self.fail_closed)
            return self._calendar_unavailable_status(f"Calendar cache unreadable — news protection unavailable ({e})")

        active_medium = None

        for event in events:
            severity = self.classify_event(event)
            if not severity:
                continue

            event_time = self.sg_tz.localize(datetime.strptime(event["time_sgt"], "%Y-%m-%d %H:%M"))
            window_start = event_time - timedelta(minutes=self.before_minutes)
            window_end = event_time + timedelta(minutes=self.after_minutes)
            if not (window_start <= now <= window_end):
                continue

            if severity == "major":
                return {
                    "blocked": True,
                    "penalty": 0,
                    "reason": f"Blocked by major news: {event['name']} at {event['time_sgt']} SGT",
                    "severity": severity,
                    "event": event,
                }
            if severity == "medium" and active_medium is None:
                active_medium = {
                    "blocked": False,
                    "penalty": self.medium_penalty,
                    "reason": f"Medium news nearby: {event['name']} at {event['time_sgt']} SGT",
                    "severity": severity,
                    "event": event,
                }

        # Lookahead: scan for upcoming events in the next N minutes (informational only)
        lookahead_events = []
        for event in events:
            severity = self.classify_event(event)
            if not severity:
                continue
            try:
                event_time = self.sg_tz.localize(datetime.strptime(event["time_sgt"], "%Y-%m-%d %H:%M"))
            except Exception:
                continue
            window_start = event_time - timedelta(minutes=self.before_minutes)
            window_end   = event_time + timedelta(minutes=self.after_minutes)
            in_window = window_start <= now <= window_end
            if not in_window:
                lookahead_end = now + timedelta(minutes=self.lookahead_minutes)
                if now <= event_time <= lookahead_end:
                    mins_away = int((event_time - now).total_seconds() // 60)
                    lookahead_events.append({
                        "name": event["name"],
                        "time_sgt": event["time_sgt"],
                        "severity": severity,
                        "mins_away": mins_away,
                    })

        result = active_medium or {"blocked": False, "penalty": 0, "reason": "No blocking news", "severity": None}
        result["lookahead"] = lookahead_events
        return result

    def is_blocked_now(self) -> tuple[bool, str]:
        status = self.get_status_now()
        return bool(status.get("blocked")), str(status.get("reason", "No blocking news"))
