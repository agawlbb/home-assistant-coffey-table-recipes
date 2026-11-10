"""Occupied-date conversion and automatic synchronization, without external IO."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo
import pytest
from homeassistant.util import dt as dt_util
from custom_components.coffey_table_recipes.work_calendar import occupied_dates, async_setup_work_calendar


def test_any_title_boundaries_and_timezone():
    zone = ZoneInfo('America/New_York')
    start = datetime(2026, 11, 1, tzinfo=zone)
    end = datetime(2026, 12, 1, tzinfo=zone)
    events = [
        {'start': '2026-11-02', 'end': '2026-11-03', 'summary': 'anything'},
        {'start': '2026-11-04', 'end': '2026-11-06', 'summary': ''},
        {'start': '2026-11-07T23:00:00-05:00', 'end': '2026-11-09T00:00:00-05:00'},
        {'start': '2026-11-10T02:00:00+00:00', 'end': '2026-11-10T03:00:00+00:00'},
        {'start': '2026-10-30', 'end': '2026-11-02'},
        {'start': '2026-11-30', 'end': '2026-12-03'},
    ]
    with patch.object(dt_util, 'DEFAULT_TIME_ZONE', zone):
        assert occupied_dates(events, start, end) == ['2026-11-01', '2026-11-02', '2026-11-04', '2026-11-05', '2026-11-07', '2026-11-08', '2026-11-09', '2026-11-30']


async def test_refresh_change_delete_unavailable_and_unload(hass):
    callbacks = []
    tasks = []
    cleanup = []
    sent = AsyncMock()
    def create_task(hass, coroutine, name):
        task = hass.async_create_task(coroutine)
        tasks.append(task)
        return task
    entry = SimpleNamespace(options={'work_calendar': 'calendar.work'},
        runtime_data=SimpleNamespace(client=SimpleNamespace(socket=SimpleNamespace(request=sent))),
        async_on_unload=cleanup.append, async_create_background_task=create_task)
    hass.states.async_set('calendar.work', 'off')
    events = [{'start': '2026-11-10', 'end': '2026-11-11', 'summary': 'not workin'}]
    async def get_events(*args, **kwargs):
        return {'calendar.work': {'events': events}}
    subscribe = Mock(return_value=Mock())
    entity = SimpleNamespace(async_subscribe_events=subscribe)
    from homeassistant.components.calendar.const import DATA_COMPONENT
    hass.data[DATA_COMPONENT] = SimpleNamespace(get_entity=lambda entity_id: entity)
    with patch('custom_components.coffey_table_recipes.work_calendar.async_track_time_interval', side_effect=lambda h,c,t: (callbacks.append(c) or Mock())), patch.object(type(hass.services), 'async_call', side_effect=get_events):
        await async_setup_work_calendar(hass, entry)
        await hass.async_block_till_done()
        sent.assert_awaited_with({'dates': ['2026-11-10']}, resource='work-calendar')
        events[:] = [{'start': '2026-11-12', 'end': '2026-11-13'}]
        subscribe.call_args.args[2](None)  # event edit without an entity state change
        await hass.async_block_till_done()
        sent.assert_awaited_with({'dates': ['2026-11-12']}, resource='work-calendar')
        events.clear()
        callbacks[0](None)
        await hass.async_block_till_done()
        sent.assert_awaited_with({'dates': []}, resource='work-calendar')
        count = sent.await_count
        hass.states.async_set('calendar.work', 'unavailable')
        await hass.async_block_till_done()
        assert sent.await_count == count  # unavailable is not an empty calendar
        for stop in cleanup:
            stop()
        callbacks[0](None)
        await hass.async_block_till_done()
        assert sent.await_count == count


async def test_calendar_option_can_be_selected_and_cleared(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.coffey_table_recipes.const import DOMAIN
    entry = MockConfigEntry(domain=DOMAIN, data={'household_id': '1', 'token': 'fixture'})
    entry.add_to_hass(hass)
    for selection in ('calendar.work', None):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(result['flow_id'], {
            'scan_interval': 30, 'work_calendar': selection,
        })
        assert result['type'] == 'create_entry'
        assert entry.options['work_calendar'] == selection


async def test_clearing_selection_publishes_empty_dates(hass):
    cleanup = []
    sent = AsyncMock()
    tasks = []
    def create_task(hass, coroutine, name):
        task = hass.async_create_task(coroutine)
        tasks.append(task)
        return task
    entry = SimpleNamespace(options={},
        runtime_data=SimpleNamespace(client=SimpleNamespace(socket=SimpleNamespace(request=sent))),
        async_on_unload=cleanup.append, async_create_background_task=create_task)
    await async_setup_work_calendar(hass, entry)
    await hass.async_block_till_done()
    sent.assert_awaited_once_with({'dates': []}, resource='work-calendar')
    for stop in cleanup:
        stop()
