"""Publish occupied dates, never calendar titles or event content."""
import asyncio
from datetime import datetime, time, timedelta
import logging
from homeassistant.core import callback
from homeassistant.components.calendar.const import DATA_COMPONENT
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util
from .shopping_socket import SocketError

_LOGGER = logging.getLogger(__name__)


def occupied_dates(events, start, end):
    """Event ends are exclusive, including timed events ending at midnight."""
    dates = set()
    for event in events:
        def boundary(value):
            parsed = dt_util.parse_datetime(value)
            if parsed is not None:
                return dt_util.as_local(parsed)
            day = dt_util.parse_date(value)
            if day is None:
                raise ValueError("Invalid calendar event date")
            return datetime.combine(day, time.min, tzinfo=start.tzinfo)
        first, last = boundary(event["start"]), boundary(event["end"])
        day = max(first.date(), start.date())
        while day < end.date() and datetime.combine(day, time.min, tzinfo=start.tzinfo) < last:
            if last > first:
                dates.add(day.isoformat())
            day += timedelta(days=1)
    return sorted(dates)


async def async_setup_work_calendar(hass, entry):
    entity_id = entry.options.get("work_calendar")
    lock = asyncio.Lock()
    stopped = False
    task = None
    last_sent = None
    unsubscribe = None
    subscribed_range = None

    async def refresh(_=None):
        nonlocal last_sent, unsubscribe, subscribed_range
        async with lock:
            if stopped:
                return
            try:
                dates = []
                if entity_id:
                    # Keep a rolling history and upcoming planning window.
                    today = dt_util.now().date()
                    start = datetime.combine(today - timedelta(days=366), time.min, tzinfo=dt_util.DEFAULT_TIME_ZONE)
                    end = datetime.combine(today + timedelta(days=731), time.min, tzinfo=dt_util.DEFAULT_TIME_ZONE)
                    component = hass.data.get(DATA_COMPONENT)
                    entity = component.get_entity(entity_id) if component else None
                    if entity is not None and subscribed_range != (start, end):
                        if unsubscribe is not None:
                            unsubscribe()
                        unsubscribe = entity.async_subscribe_events(start, end, schedule)
                        subscribed_range = (start, end)
                    state = hass.states.get(entity_id)
                    if state is None or state.state in ("unavailable", "unknown"):
                        raise HomeAssistantError("Calendar unavailable")
                    response = await hass.services.async_call("calendar", "get_events", {
                        "entity_id": entity_id, "start_date_time": start.isoformat(), "end_date_time": end.isoformat(),
                    }, blocking=True, return_response=True)
                    dates = occupied_dates(response[entity_id]["events"], start, end)
                if dates != last_sent:
                    await entry.runtime_data.client.socket.request({"dates": dates}, resource="work-calendar")
                    last_sent = dates
            except (HomeAssistantError, SocketError, ValueError, KeyError, TypeError, OSError):
                _LOGGER.error("Could not update CTR work calendar; check the selected calendar and CTR connection. Retrying automatically")

    @callback
    def schedule(_=None):
        nonlocal task
        if not stopped and (task is None or task.done()):
            task = entry.async_create_background_task(hass, refresh(), "CTR work calendar")

    if entity_id:
        entry.async_on_unload(async_track_state_change_event(hass, [entity_id], schedule))
    entry.async_on_unload(async_track_time_interval(hass, schedule, timedelta(seconds=60)))

    @callback
    def stop():
        nonlocal stopped
        stopped = True
        if unsubscribe is not None:
            unsubscribe()
        if task is not None:
            task.cancel()
    entry.async_on_unload(stop)
    schedule()
