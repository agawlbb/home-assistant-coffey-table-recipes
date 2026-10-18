"""Real HA coverage for optional Keep configuration, lifecycle and migration."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockModule, mock_integration, async_fire_time_changed

from custom_components.coffey_table_recipes.const import DOMAIN, EVENT_CHANGED, EVENT_SYNC_REQUESTED
from custom_components.coffey_table_recipes.keep_sync import (
    CONF_KEEP_LIST, CONF_KEEP_TARGET, LEGACY_DOMAIN, migrate_legacy_bridge, sync_store,
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
    assert sync.status == "synced" and sync.last_success
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


async def test_legacy_mapping_migrates_after_old_writer_stops(hass):
    keep_entry, _, entity_id = register_keep(hass)
    native = MockConfigEntry(domain=DOMAIN, unique_id="1", data={"household_id":"1","token":"fixture"})
    native.add_to_hass(hass)
    unload = AsyncMock(return_value=True)
    mock_integration(hass, MockModule(domain=LEGACY_DOMAIN, async_setup_entry=AsyncMock(return_value=True), async_unload_entry=unload))
    old = MockConfigEntry(domain=LEGACY_DOMAIN, state=ConfigEntryState.LOADED, data={**TARGET, "ctr_entry":native.entry_id,"ctr_unique":"1_shopping_list"})
    old.add_to_hass(hass)
    old.runtime_data = NS(last_success="2026-10-18T14:29:21-04:00")
    ledger = {"rows":[{"left":"c","right":"k","base":{"summary":"Milk","status":"needs_action"},
                        "pending":{"side":"right","before":["k"],"desired":{"summary":"Eggs","status":"needs_action"}}}]}
    await Store(hass,1,f"{LEGACY_DOMAIN}.{old.entry_id}").async_save(ledger)
    await migrate_legacy_bridge(hass, native)
    unload.assert_awaited_once()
    assert old.disabled_by == ConfigEntryDisabler.INTEGRATION
    saved = await sync_store(hass, native.entry_id, TARGET).async_load()
    assert saved["engine"] == ledger and saved["last_success"] == "2026-10-18T14:29:21-04:00"
    assert native.options[CONF_KEEP_TARGET] == TARGET
    assert native.options[CONF_KEEP_LIST] == entity_id
    assert await Store(hass,1,f"{LEGACY_DOMAIN}.{old.entry_id}").async_load() == ledger
    # Clearing the optional list later must not silently re-enable migration.
    hass.config_entries.async_update_entry(native, options={})
    await migrate_legacy_bridge(hass, native)
    assert native.options == {}
