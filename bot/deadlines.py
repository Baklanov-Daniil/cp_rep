import re
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def calculate_msk_deadline(day_marker: str | None, week_marker: str | None, time_marker: str | None) -> datetime | None:
    if not day_marker:
        return None

    now_msk = datetime.now(MSK)
    time_str = (time_marker or "18:00").strip()

    def apply_time(value: datetime) -> datetime:
        try:
            hour, minute = map(int, time_str.split(":"))
        except ValueError:
            hour, minute = 18, 0
        return value.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if re.match(r"^\d{4}-\d{2}-\d{2}$", day_marker):
        try:
            return apply_time(datetime.strptime(day_marker, "%Y-%m-%d").replace(tzinfo=MSK))
        except ValueError:
            return None

    if day_marker == "today":
        return apply_time(now_msk)
    if day_marker == "tomorrow":
        return apply_time(now_msk + timedelta(days=1))

    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    target_weekday = weekdays.get(day_marker.lower())
    if target_weekday is None:
        return None

    days_ahead = (target_weekday - now_msk.weekday()) % 7
    if week_marker == "next":
        days_ahead = (days_ahead or 7) + 7
    elif days_ahead == 0 and apply_time(now_msk) <= now_msk:
        days_ahead = 7

    return apply_time(now_msk + timedelta(days=days_ahead))


def choose_task_column(deadline: datetime | None, columns: dict[str, str | None]) -> str | None:
    if deadline is None:
        return columns["no_deadline"]

    if deadline - datetime.now(MSK) <= timedelta(days=2):
        return columns["urgent_deadline"]

    return columns["has_deadline"]
