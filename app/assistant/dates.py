"""Explicit local-date semantics shared by the prompt and date-resolution tool."""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def local_now(zone="Europe/London"):
    return datetime.now(ZoneInfo(zone))


def resolve_date(expression, anchor=None, zone="Europe/London"):
    import dateparser
    base = date.fromisoformat(anchor) if anchor else local_now(zone).date()
    value = expression.strip().lower()
    if value in {"today", "tomorrow", "yesterday"}:
        result = base + timedelta(days={"today": 0, "tomorrow": 1, "yesterday": -1}[value])
    elif match := re.fullmatch(r"(?:(this|next|last|upcoming)\s+)?(" + "|".join(WEEKDAYS) + ")", value):
        mode, weekday = match.groups()
        target = WEEKDAYS.index(weekday)
        delta = (target - base.weekday()) % 7
        if mode == "last":
            delta = -((base.weekday() - target) % 7 or 7)
        elif mode == "next" and delta == 0:
            delta = 7
        result = base + timedelta(days=delta)
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        result = date.fromisoformat(value)
    else:
        if re.fullmatch(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?", value):
            raise ValueError("Numeric dates are ambiguous. Use a month name or YYYY-MM-DD.")
        parsed = dateparser.parse(expression, languages=["en"], settings={
            "RELATIVE_BASE": datetime.combine(base, datetime.min.time()), "DATE_ORDER": "DMY",
            "PREFER_DAY_OF_MONTH": "current", "PREFER_MONTH_OF_YEAR": "current"})
        if parsed is None:
            raise ValueError("Could not resolve that date. Ask for a full date.")
        result = parsed.date()
    return {"date": result.isoformat(), "weekday": WEEKDAYS[result.weekday()], "anchor": base.isoformat(), "timezone": zone}
