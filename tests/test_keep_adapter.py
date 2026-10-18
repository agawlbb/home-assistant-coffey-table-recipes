"""Adapter tests with remote clients mocked; no live list writes."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch


def stub(name, **attributes):
    result = ModuleType(name)
    result.__dict__.update(attributes)
    return result


class HAError(Exception):
    pass


def mark_callback(function):
    function._hass_callback = True
    return function


fake_modules = {
    "voluptuous": stub("voluptuous"),
    "homeassistant": stub("homeassistant"),
    "homeassistant.const": stub("homeassistant.const", Platform=NS(SENSOR="sensor")),
    "homeassistant.core": stub("homeassistant.core", SupportsResponse=NS(OPTIONAL="optional"), callback=mark_callback),
    "homeassistant.exceptions": stub("homeassistant.exceptions", ConfigEntryNotReady=HAError, HomeAssistantError=HAError),
    "homeassistant.helpers": stub("homeassistant.helpers", entity_registry=NS(async_get=lambda hass: hass.registry)),
    "homeassistant.helpers.dispatcher": stub("homeassistant.helpers.dispatcher", async_dispatcher_send=lambda *a: None),
    "homeassistant.helpers.event": stub("homeassistant.helpers.event", async_track_time_interval=lambda *a: lambda: None),
    "homeassistant.helpers.storage": stub("homeassistant.helpers.storage", Store=lambda *a: NS(async_save=AsyncMock(), async_load=AsyncMock(return_value=None))),
    "homeassistant.util": stub("homeassistant.util"),
    "homeassistant.util.dt": stub("homeassistant.util.dt", utcnow=lambda: NS(isoformat=lambda: "now")),
}
directory = Path(__file__).parents[1] / "custom_components/coffey_table_recipes"
spec = importlib.util.spec_from_file_location("bridge_adapter_test", directory / "keep_sync.py", submodule_search_locations=[str(directory)])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
with patch.dict(sys.modules, fake_modules):
    spec.loader.exec_module(module)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def make_bridge(self):
        entry = NS(entry_id="bridge", data={"ctr_entry": "ctr", "keep_entry": "keep", "keep_list_id": "list",
                                         "ctr_unique": "ctr_unique", "keep_unique": "keep_unique"})
        ctr = NS()
        import asyncio
        ctr._snapshot_lock = asyncio.Lock()
        ctr.client = NS(read=AsyncMock(return_value={"items": [{"id": "c", "name": "Milk", "checked": False}]}))
        ctr.accept = lambda state: state
        ctr.async_set_updated_data = Mock()
        ctr.change = AsyncMock()
        keep = NS(api=NS(fetch_all_lists=AsyncMock(return_value=[NS(id="list", trashed=False, deleted=False,
                    items=[NS(id="k", text="Milk", checked=False, deleted=False)])]),
                    async_create_todo_item=AsyncMock(), async_delete_todo_item=AsyncMock(), async_update_todo_item=AsyncMock()),
                  config_entry=NS(data={"lists_to_sync": ["list"]}), async_set_updated_data=Mock())
        entries = {"ctr": NS(state=NS(value="loaded"), entry_id="ctr", runtime_data=ctr),
                   "keep": NS(state=NS(value="loaded"), entry_id="keep", data={"lists_to_sync": ["list"]})}
        registry = NS(async_get_entity_id=lambda domain, platform, unique: platform,
                      async_get=lambda entity: NS(config_entry_id="ctr" if entity == "coffey_table_recipes" else "keep", disabled_by=None))
        hass = NS(config_entries=NS(async_get_entry=entries.get, async_entries=lambda domain: []), registry=registry,
                  data={"google_keep_sync": {"keep": keep}})
        bridge = module.KeepSync(hass, entry, entry.data)
        return bridge, ctr, keep

    async def test_fresh_read_maps_only_selected_lists(self):
        bridge, ctr, keep = self.make_bridge()
        result = await bridge.read()
        self.assertEqual(result["left"]["c"], result["right"]["k"])
        ctr.async_set_updated_data.assert_called_once()
        keep.async_set_updated_data.assert_called_once()

    async def test_keep_network_failure_does_not_read_or_write_ctr(self):
        bridge, ctr, keep = self.make_bridge()
        keep.api.fetch_all_lists.side_effect = ConnectionError()
        result = await bridge.sync("ctr_request")
        self.assertEqual(result["status"], "not_confirmed")
        ctr.client.read.assert_not_awaited()
        ctr.change.assert_not_awaited()
        self.assertIsNone(result["last_success"])

    async def test_ctr_network_failure_cannot_look_like_empty_list(self):
        bridge, ctr, keep = self.make_bridge()
        ctr.client.read.side_effect = ConnectionError()
        result = await bridge.sync("poll")
        self.assertEqual(result["status"], "not_confirmed")
        keep.api.async_delete_todo_item.assert_not_awaited()

    async def test_deleted_or_trashed_list_is_not_item_deletions(self):
        bridge, ctr, keep = self.make_bridge()
        keep.api.fetch_all_lists.return_value = []
        with self.assertRaises(module.SyncBlocked):
            await bridge.read()
        ctr.change.assert_not_awaited()

    async def test_deselected_keep_list_blocks(self):
        bridge, ctr, keep = self.make_bridge()
        bridge.hass.config_entries.async_get_entry("keep").data["lists_to_sync"] = []
        with self.assertRaises(module.SyncBlocked):
            await bridge.read()
        keep.api.fetch_all_lists.assert_not_awaited()

    async def test_unload_blocks_further_writes(self):
        bridge, ctr, keep = self.make_bridge()
        bridge.closed = True
        with self.assertRaises(module.SyncBlocked):
            await bridge.write("left", "delete", "c", None)
        ctr.change.assert_not_awaited()

    async def test_selected_list_identity_must_still_match(self):
        bridge, ctr, keep = self.make_bridge()
        bridge.hass.registry.async_get = lambda entity: NS(config_entry_id="wrong", disabled_by=None)
        with self.assertRaises(module.SyncBlocked):
            await bridge.read()

    async def test_sync_success_requires_full_final_read(self):
        bridge, ctr, keep = self.make_bridge()
        result = await bridge.sync("ctr_request")
        self.assertEqual(result["status"], "synced")
        self.assertEqual(result["last_success"], "now")
        self.assertEqual(keep.api.fetch_all_lists.await_count, 2)

    async def test_self_changes_do_not_queue_but_ctr_requests_do(self):
        bridge, ctr, keep = self.make_bridge()
        await bridge.lock.acquire()
        bridge.schedule("ctr_change")
        self.assertFalse(bridge.again)
        bridge.schedule("ctr_request")
        self.assertTrue(bridge.again)
        bridge.lock.release()


if __name__ == "__main__":
    unittest.main()
