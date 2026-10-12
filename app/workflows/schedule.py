"""购买时段与配置时区的纯规则。"""

from datetime import datetime, tzinfo

from utils.time import now_in_configured_timezone, resolve_configured_timezone


def _resolve_schedule_timezone(system_config: dict) -> tuple[tzinfo | None, str]:
    return resolve_configured_timezone(system_config)


def _now_in_schedule_timezone(schedule_timezone: tzinfo | None) -> datetime:
    return now_in_configured_timezone(schedule_timezone)


def _is_in_time_window(
    start_hour: int,
    end_hour: int,
    current_time: datetime | None = None,
) -> bool:
    hour = (
        current_time
        if current_time is not None
        else datetime.now().astimezone()
    ).hour
    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour
