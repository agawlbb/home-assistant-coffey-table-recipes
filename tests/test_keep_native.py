"""Real HA coverage for optional Keep configuration and lifecycle."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockModule, mock_integration, async_fire_time_changed

from custom_components.coffey_table_recipes.const import DOMAIN, EVENT_CHANGED, EVENT_SYNC_REQUESTED
from custom_components.coffey_table_recipes.keep_sync import (
    CONF_KEEP_LIST, CONF_KEEP_TARGET,
)

pytestmark = pytest.mark.asyncio
STATE = {"revision":1, "items":[{"id":"c", "name":"Milk", "checked":False, "department":"Dairy"}], "departments":["Dairy"]}
TARGET = {"keep_entry":"keep", "keep_unique":"google_keep_sync.list.list", "keep_list_id":"list"}


def register_keep(hass):
    mock_integration(hass, MockModule(domain="google_keep_sync", async_setup_entry=AsyncMock(return_value=True), async_unload_entry=AsyncMock(return_value=True)))
    # The external custom integration is already loaded; its source is not part
    # of this repository/test install. Exercise the real CTR lifecycle around it.
    keep_entry = MockConfigEntry(domain="google_keep_sync", entry_id="keep", state=ConfigEntryState.LOADED, data={"lists_to_sync":["list"]})
    keep_entry.add_to_hass(hass)
    hass.config.components.add("google_keep_sync")
    entity = er.async_get(hass).async_get_or_create("todo", "google_keep_sync", TARGET["keep_unique"], config_entry=keep_entry, suggested_object_id="keep_fixture")
    api = NS(fetch_all_lists=AsyncMock(return_value=[NS(id="list", items=[NS(id="k",text="Milk",checked=False)])]),
             async_create_todo_item=AsyncMock(), async_update_todo_item=AsyncMock(), async_delete_todo_item=AsyncMock())
    keep = NS(api=api, config_entry=keep_entry, async_set_updated_data=Mock())
    hass.data["google_keep_sync"] = {"keep":keep}
    return keep_entry, keep, entity.entity_id


@pytest.fixture
async def pair(hass):
    keep_entry, keep, entity_id = register_keep(hass)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="1", title="Family", data={"household_id":"1","token":"fixture"},
                            options={CONF_KEEP_LIST:entity_id, CONF_KEEP_TARGET:TARGET})
    entry.add_to_hass(hass)
    with patch("custom_components.coffey_table_recipes.api.Client.read", return_value=deepcopy(STATE)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await entry.runtime_data.keep_sync.task
        yield entry, keep_entry, keep, entity_id


async def test_native_requests_are_scoped_and_bypass_cooldown(hass, pair):
    entry, _, keep, _ = pair
    sync = entry.runtime_data.keep_sync
    assert sync.status == "synced" and sync.last_success, sync.last_error
    keep.api.fetch_all_lists.reset_mock()
    hass.bus.async_fire(EVENT_SYNC_REQUESTED, {"config_entry_id":"different"})
    await hass.async_block_till_done()
    keep.api.fetch_all_lists.assert_not_awaited()
    for _ in range(2):
        hass.bus.async_fire(EVENT_SYNC_REQUESTED, {"config_entry_id":entry.entry_id})
        await hass.async_block_till_done()
        await sync.task
    assert keep.api.fetch_all_lists.await_count == 4
    assert sync.last_reason == "ctr_request"


async def test_native_ctr_and_keep_edits_and_one_minute_poll(hass, pair):
    entry, _, keep, entity_id = pair
    sync = entry.runtime_data.keep_sync
    for event, payload, reason in [
        (EVENT_CHANGED, {"config_entry_id":entry.entry_id}, "ctr_change"),
        ("call_service", {"domain":"todo","service":"update_item","service_data":{"entity_id":entity_id}}, "keep_ha_change"),
    ]:
        hass.bus.async_fire(event, payload)
        await hass.async_block_till_done()
        await sync.task
        assert sync.last_reason == reason
        assert sync.status == "synced", sync.last_error
    async_fire_time_changed(hass, dt_util.utcnow()+timedelta(seconds=61))
    await hass.async_block_till_done()
    await sync.task
    assert sync.last_reason == "poll"


async def test_failed_keep_read_never_mutates_ctr(hass, pair):
    entry, _, keep, _ = pair
    sync = entry.runtime_data.keep_sync
    previous_success = sync.last_success
    keep.api.fetch_all_lists.side_effect = ConnectionError("offline")
    with patch.object(entry.runtime_data, "change") as change:
        result = await sync.sync("ctr_request")
    assert result["status"] == "not_confirmed"
    assert result["last_success"] == previous_success
    keep.api.fetch_all_lists.assert_awaited()
    change.assert_not_called()


async def test_ledger_and_success_persist_and_unload_stops_sync(hass, pair):
    entry, _, _, _ = pair
    sync = entry.runtime_data.keep_sync
    saved = await sync.store.async_load()
    assert saved["engine"]["rows"][0]["left"] == "c"
    assert saved["last_success"] == sync.last_success
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert sync.closed
    sync.schedule("ctr_request")
    assert sync.task.done()


async def test_optional_selector_validates_identity_and_can_disable(hass, pair):
    entry, _, _, entity_id = pair
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {"scan_interval":10, CONF_KEEP_LIST:"todo.unknown"})
    assert result["errors"][CONF_KEEP_LIST] == "invalid_keep_list"
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {"scan_interval":10, CONF_KEEP_LIST:None})
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert entry.options == {"scan_interval":10}
    assert entry.runtime_data.keep_sync is None



async def test_only_native_sync_services_are_registered(hass, pair):
    assert set(hass.services.async_services()[DOMAIN]) == {"sync_google_keep", "sync_icloud"}


async def test_obsolete_marker_cleanup_preserves_other_connection_data(hass, pair):
    entry, _, _, _ = pair
    assert await hass.config_entries.async_unload(entry.entry_id)
    original = {**entry.data, "future_metadata": "keep me"}
    hass.config_entries.async_update_entry(entry, data={**original, "keep_bridge_migrated":"removed"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.keep_sync.task
    assert dict(entry.data) == original
    assert entry.runtime_data.keep_sync.status == "synced"
