"""iCloud adapter coverage using real HA lifecycle and mocked remote reminders."""
from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockModule, mock_integration, async_fire_time_changed

from custom_components.coffey_table_recipes.const import DOMAIN, EVENT_SYNC_REQUESTED, EVENT_CHANGED
from custom_components.coffey_table_recipes.icloud_sync import (
    CONF_ICLOUD_LIST, CONF_ICLOUD_TARGET, IcloudSync, resolve_icloud_selection,
)
from custom_components.coffey_table_recipes.keep_engine import SyncBlocked

pytestmark = pytest.mark.asyncio
STATE = {"revision":1, "items":[{"id":"c", "name":"Milk", "checked":False, "department":"Dairy"}], "departments":["Dairy"]}


@pytest.fixture
async def apple(hass):
    mock_integration(hass, MockModule(domain="icloud", async_setup_entry=AsyncMock(return_value=True), async_unload_entry=AsyncMock(return_value=True)))
    owner = MockConfigEntry(domain="icloud", unique_id="apple-account", state=ConfigEntryState.LOADED)
    owner.add_to_hass(hass)
    hass.config.components.add("icloud")
    unique = "apple-account_list-1"
    entity = er.async_get(hass).async_get_or_create("todo", "icloud", unique, config_entry=owner, suggested_object_id="apple_fixture")
    reminder = NS(uid="a",summary="Milk",completed=False,description="Apple notes",due=date(2026,10,25))
    coordinator = NS(async_refresh=AsyncMock(), async_add_listener=Mock(return_value=Mock()), last_update_success=True, _skipped_undecoded=False,
                     data={"list-1":NS(reminders=[reminder])})
    owner.runtime_data = NS(reminders_coordinator=coordinator)
    target = {"icloud_entry":owner.entry_id,"icloud_unique":unique}
    entry = MockConfigEntry(domain=DOMAIN, unique_id="1", title="Family", data={"household_id":"1","token":"fixture"},
                            options={CONF_ICLOUD_LIST:entity.entity_id,CONF_ICLOUD_TARGET:target})
    entry.add_to_hass(hass)
    with patch("custom_components.coffey_table_recipes.api.Client.read", return_value=deepcopy(STATE)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await entry.runtime_data.icloud_sync.task
        yield entry, owner, coordinator, entity.entity_id


async def test_initial_mapping_and_status_are_persistent(hass, apple):
    entry, _, remote, entity_id = apple
    sync = entry.runtime_data.icloud_sync
    assert sync.status == "synced", sync.last_error
    assert sync.state["rows"] == [{"left":"c","right":"a","base":{"summary":"Milk","status":"needs_action"}}]
    assert (await sync.store.async_load())["last_success"] == sync.last_success
    state = hass.states.get("sensor.family_apple_icloud_synchronization")
    assert state.state == "synced" and state.attributes["icloud_entity_id"] == entity_id
    assert resolve_icloud_selection(hass, entity_id, entry.entry_id) == sync.target()
    replacement = IcloudSync(hass,entry,sync.selection)
    await replacement.load()
    assert replacement.state == sync.state and replacement.last_success == sync.last_success


async def test_failed_or_partial_refresh_never_becomes_deletion(hass, apple):
    entry, _, remote, _ = apple
    sync = entry.runtime_data.icloud_sync
    previous = sync.last_success
    for field, value in [("last_update_success",False),("_skipped_undecoded",True),("data",{})]:
        original = getattr(remote,field)
        setattr(remote,field,value)
        with patch.object(sync,"write") as write:
            result = await sync.sync("manual")
        assert result["status"] == "not_confirmed" and result["last_success"] == previous
        write.assert_not_called()
        setattr(remote,field,original)


async def test_apple_update_preserves_notes_and_due_date(hass, apple):
    entry, _, _, entity_id = apple
    sync = entry.runtime_data.icloud_sync
    with patch.object(hass.services,"async_call",new_callable=AsyncMock) as service:
        await sync.write("right","update","a",{"summary":"Oat milk","status":"completed"})
    service.assert_awaited_once_with("todo","update_item",{
        "entity_id":entity_id,"item":"a","rename":"Oat milk","status":"completed",
        "description":"Apple notes","due_date":"2026-10-25"},blocking=True)


async def test_scoped_requests_ctr_edits_ha_edits_and_poll(hass, apple):
    entry, _, remote, entity_id = apple
    sync = entry.runtime_data.icloud_sync
    remote.async_refresh.reset_mock()
    hass.bus.async_fire(EVENT_SYNC_REQUESTED,{"config_entry_id":"other"})
    await hass.async_block_till_done()
    remote.async_refresh.assert_not_awaited()
    for event,data in [(EVENT_SYNC_REQUESTED,{"config_entry_id":entry.entry_id}),
                       (EVENT_CHANGED,{"config_entry_id":entry.entry_id}),
                       ("call_service",{"domain":"todo","service":"update_item","service_data":{"entity_id":entity_id}})]:
        hass.bus.async_fire(event,data)
        await hass.async_block_till_done()
        await sync.task
        assert sync.status == "synced", sync.last_error
    async_fire_time_changed(hass,dt_util.utcnow()+timedelta(seconds=61))
    await hass.async_block_till_done()
    await sync.task
    assert sync.last_reason == "poll"
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert sync.closed and sync.task.done()


async def test_optional_selector_rejects_wrong_provider_and_clears(hass, apple):
    entry, _, _, _ = apple
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"],{"scan_interval":10,CONF_ICLOUD_LIST:"todo.unknown"})
    assert result["errors"][CONF_ICLOUD_LIST] == "invalid_icloud_list"
    result = await hass.config_entries.options.async_configure(flow["flow_id"],{"scan_interval":10,CONF_ICLOUD_LIST:None})
    await hass.async_block_till_done()
    assert result["type"] == "create_entry" and entry.options == {"scan_interval":10}
    assert entry.runtime_data.icloud_sync is None


async def test_another_household_cannot_claim_same_apple_list(hass, apple):
    entry, _, _, entity_id = apple
    with pytest.raises(SyncBlocked,match="another CTR household"):
        resolve_icloud_selection(hass,entity_id,"different-household")


async def test_bidirectional_engine_writes_keep_ctr_department(hass, apple):
    entry, _, remote, entity_id = apple
    sync = entry.runtime_data.icloud_sync
    remote.data["list-1"].reminders[0].summary = "Oat milk"
    with patch.object(entry.runtime_data,"change",new_callable=AsyncMock) as change:
        async def changed(operation, **kwargs):
            assert operation == "update" and kwargs == {"uid":"c","name":"Oat milk","checked":False}
            # Server Client.mutate preserves the original department (covered by API tests).
            state = deepcopy(STATE)
            state["items"][0]["name"] = "Oat milk"
            entry.runtime_data.client.read.return_value = state
        change.side_effect = changed
        result = await sync.sync("manual")
        assert result["status"] == "synced", result
        change.assert_awaited_once()
        assert (await entry.runtime_data.client.read())["items"][0]["department"] == "Dairy"
