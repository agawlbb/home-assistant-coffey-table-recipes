"""Exercise real HA config entries, entities, services, and event delivery."""
from copy import deepcopy
from unittest.mock import AsyncMock, patch
import pytest
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.components.todo import TodoItem, TodoItemStatus
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.coffey_table_recipes.api import ApiError
from custom_components.coffey_table_recipes.const import DOMAIN, EVENT_CHANGED, EVENT_SYNC_REQUESTED

pytestmark = pytest.mark.asyncio
STATE = {"revision": 1, "items": [
    {"id": "milk", "name": "Milk", "checked": False, "department": "Dairy"},
    {"id": "bread", "name": "Bread", "checked": True, "department": ""}], "departments": ["Dairy"]}


def entry():
    return MockConfigEntry(domain=DOMAIN, unique_id="1", title="Family", data={"household_id":"1", "token":"fixture-token"})


async def setup(hass):
    config = entry()
    config.add_to_hass(hass)
    with patch("custom_components.coffey_table_recipes.api.Client.read", return_value=deepcopy(STATE)):
        assert await hass.config_entries.async_setup(config.entry_id)
        await hass.async_block_till_done()
    return config


async def test_sign_in_creates_household_entry_without_password(hass):
    with patch("custom_components.coffey_table_recipes.api.Client.sign_in", return_value=("id-token", [{"id":"1", "name":"Family", "role":"owner"}])), \
         patch("custom_components.coffey_table_recipes.api.Client.connect", return_value={"token":"household-token"}), \
         patch("custom_components.coffey_table_recipes.api.Client.read", return_value=STATE), \
         patch("custom_components.coffey_table_recipes.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source":"user"})
        assert result["step_id"] == "user"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"email":"owner@example.com", "password":"secret"})
        assert result["type"] == "create_entry"
        assert result["data"] == {"household_id":"1", "token":"household-token"}
        assert result["result"].unique_id == "1"


async def test_invalid_login_and_member_only_account(hass):
    with patch("custom_components.coffey_table_recipes.api.Client.sign_in", side_effect=ApiError(400)):
        flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source":"user"})
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"email":"x@example.com", "password":"bad"})
        assert result["errors"]["base"] == "invalid_auth"
    with patch("custom_components.coffey_table_recipes.api.Client.sign_in", return_value=("id", [{"id":"1", "name":"Family", "role":"member"}])):
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"email":"x@example.com", "password":"valid"})
        assert result["errors"]["base"] == "no_household"


async def test_duplicate_household_does_not_issue_another_token(hass):
    config = entry(); config.add_to_hass(hass)
    with patch("custom_components.coffey_table_recipes.api.Client.sign_in", return_value=("id", [{"id":"1", "name":"Family", "role":"admin"}])), \
         patch("custom_components.coffey_table_recipes.api.Client.connect") as connect:
        flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source":"user"})
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"email":"x@example.com", "password":"valid"})
        assert result["reason"] == "already_configured"
        connect.assert_not_called()


async def test_entities_all_items_events_and_unload(hass):
    config = await setup(hass)
    todos = hass.states.async_all("todo")
    assert len(todos) == 1 and todos[0].state == "1"
    result = await hass.services.async_call("todo", "get_items", {"entity_id":todos[0].entity_id}, blocking=True, return_response=True)
    assert len(result[todos[0].entity_id]["items"]) == 2
    coordinator = config.runtime_data
    received = []
    unsubscribe = hass.bus.async_listen(EVENT_CHANGED, received.append)
    changed = deepcopy(STATE); changed["revision"] = 2; changed["items"][0]["checked"] = True
    coordinator.async_set_updated_data(coordinator.accept(changed))
    await hass.async_block_till_done()
    assert len(received) == 1
    assert received[0].data["updated"][0]["id"] == "milk"
    event = hass.states.async_all("event")[0]
    assert event.attributes["event_type"] == "changed"
    coordinator.async_set_updated_data(coordinator.accept(deepcopy(changed)))
    await hass.async_block_till_done()
    assert len(received) == 1
    unsubscribe()
    assert await hass.config_entries.async_unload(config.entry_id)


async def test_services_save_and_auth_failure(hass):
    config = await setup(hass)
    entity_id = hass.states.async_all("todo")[0].entity_id
    with patch.object(config.runtime_data.client, "mutate", return_value=deepcopy(STATE)) as mutate:
        await hass.services.async_call("todo", "add_item", {"entity_id":entity_id, "item":"Eggs"}, blocking=True)
        mutate.assert_awaited_once_with("add", name="Eggs")
    with patch.object(config.runtime_data.client, "mutate", side_effect=ApiError(401)):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call("todo", "remove_item", {"entity_id":entity_id, "item":"milk"}, blocking=True)
    await hass.async_block_till_done()
    assert any(flow["context"]["source"] == "reauth" for flow in hass.config_entries.flow.async_progress())


async def test_options(hass):
    config = await setup(hass)
    result = await hass.config_entries.options.async_init(config.entry_id)
    assert result["step_id"] == "init"
    with patch("custom_components.coffey_table_recipes.api.Client.read", return_value=deepcopy(STATE)):
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"scan_interval":30})
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert config.options["scan_interval"] == 30


async def test_reauth_preserves_household(hass):
    config = entry(); config.add_to_hass(hass)
    with patch("custom_components.coffey_table_recipes.api.Client.sign_in", return_value=("id", [{"id":"2", "name":"Other", "role":"owner"}])):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source":"reauth", "entry_id":config.entry_id}, data=config.data)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"email":"other@example.com", "password":"valid"})
        assert result["errors"]["base"] == "no_household"


async def test_sync_requests_are_separate_persistent_and_not_repeated(hass):
    config = await setup(hass)
    coordinator = config.runtime_data
    sync_events, list_events = [], []
    @callback
    def capture_sync(event):
        sync_events.append(event)
    unsub_sync = hass.bus.async_listen(EVENT_SYNC_REQUESTED, capture_sync)
    unsub_list = hass.bus.async_listen(EVENT_CHANGED, list_events.append)
    state = deepcopy(STATE)
    state["sync_requests"] = {"latest_id":2, "requests":[
        {"id":i,"requested_at":"2026-10-18T17:00:00+00:00","reason":"shopping_list_opened"} for i in (1,2)
    ]}
    coordinator.async_set_updated_data(coordinator.accept(state))
    await hass.async_block_till_done()
    assert [event.data["request_id"] for event in sync_events] == [1,2]
    assert list_events == []
    assert config.data["sync_request_cursor"] == 2
    event = next(e for e in hass.states.async_all("event") if e.attributes.get("event_type") == "requested")
    assert event.attributes["request_id"] == 2
    coordinator.async_set_updated_data(coordinator.accept(deepcopy(state)))
    await hass.async_block_till_done()
    assert len(sync_events) == 2
    with patch("custom_components.coffey_table_recipes.api.Client.read", return_value=state):
        await hass.config_entries.async_reload(config.entry_id)
        await hass.async_block_till_done()
    assert len(sync_events) == 2
    unsub_sync(); unsub_list()


async def test_initial_sync_history_is_not_replayed(hass):
    config = entry(); config.add_to_hass(hass)
    state = deepcopy(STATE)
    state["sync_requests"] = {"latest_id":4,"requests":[
        {"id":4,"requested_at":"2026-10-18T17:00:00+00:00","reason":"shopping_list_opened"}
    ]}
    events = []
    unsubscribe = hass.bus.async_listen(EVENT_SYNC_REQUESTED, events.append)
    with patch("custom_components.coffey_table_recipes.api.Client.read", return_value=state):
        assert await hass.config_entries.async_setup(config.entry_id)
        await hass.async_block_till_done()
    assert events == [] and config.data["sync_request_cursor"] == 4
    unsubscribe()
