"""Native CTR/Keep synchronization. Uses existing authenticated integrations, not new tokens.

Google Keep Sync 1.1.0 catches network errors in its coordinator and returns
cached lists. We therefore use its raising fetch_all_lists API to establish
fresh evidence, and read back every write before advancing our durable ledger.
"""
import asyncio
import hashlib
from datetime import timedelta
import logging

import voluptuous as vol
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util.dt import utcnow

from .keep_engine import Engine, SyncBlocked
from .const import DOMAIN, EVENT_CHANGED, EVENT_SYNC_REQUESTED

_LOGGER = logging.getLogger(__name__)

CONF_KEEP_LIST = "google_keep_list"
CONF_KEEP_TARGET = "google_keep_target"


def target_identity(selection):
    return {key: selection[key] for key in ("keep_entry", "keep_unique", "keep_list_id")}


def sync_store(hass, entry_id, selection):
    identity = f"{selection['keep_entry']}:{selection['keep_unique']}"
    suffix = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return Store(hass, 1, f"{DOMAIN}.keep_sync.{entry_id}.{suffix}")


def resolve_keep_selection(hass, entity_id, ctr_entry_id):
    """Persist registry identity, so a display name/entity ID rename is safe."""
    registry = er.async_get(hass)
    entity = registry.async_get(entity_id)
    if (not entity or entity.domain != "todo" or entity.platform != "google_keep_sync"
            or entity.disabled_by or not entity.unique_id.startswith("google_keep_sync.list.")):
        raise SyncBlocked("Select an enabled Google Keep Sync to-do list")
    owner = hass.config_entries.async_get_entry(entity.config_entry_id)
    list_id = entity.unique_id.removeprefix("google_keep_sync.list.")
    if not owner or owner.domain != "google_keep_sync" or list_id not in owner.data.get("lists_to_sync", []):
        raise SyncBlocked("This list is no longer selected in Google Keep Sync")
    selection = {"keep_entry": owner.entry_id, "keep_unique": entity.unique_id, "keep_list_id": list_id}
    for other in hass.config_entries.async_entries(DOMAIN):
        if other.entry_id != ctr_entry_id and other.options.get(CONF_KEEP_TARGET) == selection:
            raise SyncBlocked("This Keep list is already synchronized by another CTR household")
    return selection


async def async_setup_keep_sync(hass, entry):
    coordinator = entry.runtime_data
    target = entry.options.get(CONF_KEEP_TARGET)
    coordinator.keep_sync = None
    if not target:
        return
    selection = {**target, "ctr_entry": entry.entry_id,
                 "ctr_unique": f"{entry.data['household_id']}_shopping_list"}
    sync = KeepSync(hass, entry, selection)
    await sync.load()
    coordinator.keep_sync = sync

    @callback
    def ctr_event(event):
        if event.data.get("config_entry_id") == entry.entry_id:
            sync.schedule("ctr_request" if event.event_type == EVENT_SYNC_REQUESTED else "ctr_change")

    @callback
    def todo_event(event):
        if event.data.get("domain") != "todo" or event.data.get("service") == "get_items":
            return
        targets = event.data.get("service_data", {}).get("entity_id", [])
        if isinstance(targets, str):
            targets = [targets]
        if sync.keep_entity_id() in targets:
            sync.schedule("keep_ha_change")

    @callback
    def periodic(now):
        sync.schedule("poll")

    entry.async_on_unload(hass.bus.async_listen(EVENT_CHANGED, ctr_event))
    entry.async_on_unload(hass.bus.async_listen(EVENT_SYNC_REQUESTED, ctr_event))
    entry.async_on_unload(hass.bus.async_listen("call_service", todo_event))
    entry.async_on_unload(async_track_time_interval(hass, periodic, timedelta(seconds=60)))
    sync.schedule("startup")


async def async_stop_keep_sync(entry):
    sync = getattr(entry.runtime_data, "keep_sync", None)
    if sync is None:
        return
    sync.closed = True
    if sync.task and not sync.task.done():
        sync.task.cancel()
        try:
            await sync.task
        except asyncio.CancelledError:
            pass


def async_register_keep_service(hass):
    if hass.services.has_service(DOMAIN, "sync_google_keep"):
        return

    async def sync_service(call):
        configured = [entry for entry in hass.config_entries.async_entries(DOMAIN)
                      if entry.state.value == "loaded" and getattr(entry.runtime_data, "keep_sync", None)]
        entry_id = call.data.get("config_entry_id")
        selected = next((e for e in configured if e.entry_id == entry_id), None) if entry_id else (
            configured[0] if len(configured) == 1 else None)
        if selected is None:
            raise HomeAssistantError("Select one CTR household with Google Keep synchronization enabled")
        result = await selected.runtime_data.keep_sync.sync("manual")
        if result["status"] != "synced":
            raise HomeAssistantError(result["error"])
        return result

    hass.services.async_register(DOMAIN, "sync_google_keep", sync_service,
        schema=vol.Schema({vol.Optional("config_entry_id"): str}), supports_response=SupportsResponse.OPTIONAL)

class KeepSync:
    def __init__(self, hass, entry, selection):
        self.hass, self.entry = hass, entry
        self.selection = selection
        self.store = sync_store(hass, entry.entry_id, selection)
        self.state = None
        self.lock = asyncio.Lock()
        self.task = None
        self.again = False
        self.closed = False
        self.status = "not_confirmed"
        self.last_success = None
        self.last_reason = None
        self.last_error = None
        self.changes = 0

    async def load(self):
        saved = await self.store.async_load()
        if saved is not None:
            if saved.get("target") != target_identity(self.selection):
                raise SyncBlocked("Saved Keep mapping belongs to a different list")
            self.state = saved["engine"]
            self.last_success = saved.get("last_success")

    def keep_entity_id(self):
        return er.async_get(self.hass).async_get_entity_id("todo", "google_keep_sync", self.selection["keep_unique"])

    def clients(self):
        if self.closed:
            raise SyncBlocked("Keep synchronization is unloading; no further writes will be sent")
        ctr_entry = self.hass.config_entries.async_get_entry(self.selection["ctr_entry"])
        keep_entry = self.hass.config_entries.async_get_entry(self.selection["keep_entry"])
        # This adapter is started after CTR's entities/coordinator are ready but
        # before HA necessarily finishes marking our own entry as loaded.
        if (not ctr_entry or not keep_entry or ctr_entry.state.value not in ("loaded", "setup_in_progress")
                or keep_entry.state.value != "loaded"):
            raise SyncBlocked("A shopping-list integration is not loaded")
        ctr = ctr_entry.runtime_data
        keep = self.hass.data.get("google_keep_sync", {}).get(keep_entry.entry_id)
        if keep is None or not hasattr(keep.api, "fetch_all_lists"):
            raise SyncBlocked("Google Keep Sync API is unavailable")
        registry = er.async_get(self.hass)
        for platform, unique, owner in (
            ("coffey_table_recipes", self.selection["ctr_unique"], ctr_entry.entry_id),
            ("google_keep_sync", self.selection["keep_unique"], keep_entry.entry_id),
        ):
            entity_id = registry.async_get_entity_id("todo", platform, unique)
            entity = registry.async_get(entity_id) if entity_id else None
            if not entity or entity.config_entry_id != owner or entity.disabled_by:
                raise SyncBlocked("The selected list identity is unavailable")
        if self.selection["keep_list_id"] not in keep_entry.data.get("lists_to_sync", []):
            raise SyncBlocked("The Keep list is no longer selected for synchronization")
        return ctr, keep

    async def read(self):
        ctr, keep = self.clients()
        # This API propagates authentication/network errors. Neither an unavailable
        # source nor a failed refresh can be interpreted as an empty/deleted list.
        lists = await keep.api.fetch_all_lists()
        keep_list = next((item for item in lists if item.id == self.selection["keep_list_id"]), None)
        if keep_list is None or getattr(keep_list, "trashed", False) or getattr(keep_list, "deleted", False):
            raise SyncBlocked("The selected Keep list is missing or in trash")
        async with ctr._snapshot_lock:
            snapshot = await ctr.client.read()
            ctr.async_set_updated_data(ctr.accept(snapshot))
        keep_items = [item for item in keep_list.items if not getattr(item, "deleted", False)]
        mapped_keep_ids = {row.get("right") for row in (self.state or {}).get("rows", [])}
        if any(item.id in mapped_keep_ids and not item.text.strip() for item in keep_items):
            raise SyncBlocked("A previously synchronized Keep item is blank; restore its text before syncing")
        result = {
            "left": {item["id"]: {"summary": item["name"], "status": "completed" if item["checked"] else "needs_action"}
                     for item in snapshot["items"]},
            "right": {item.id: {"summary": item.text, "status": "completed" if item.checked else "needs_action"}
                      for item in keep_items if item.text.strip()},
        }
        for side in result.values():
            if len(side) > 1000 or any(not item["summary"].strip() for item in side.values()):
                raise SyncBlocked("List exceeds 1000 items or contains a blank shopping item; sync stopped")
        # Re-publish only after a successful remote read, so HA's Keep card reflects
        # this sync without waiting for the upstream 15-minute polling interval.
        selected = keep.config_entry.data.get("lists_to_sync", [])
        keep.async_set_updated_data([item for item in lists if item.id in selected])
        return result

    async def write(self, side, operation, uid, item):
        ctr, keep = self.clients()
        if side == "left":
            if operation == "add":
                await ctr.change("add", name=item["summary"])
            elif operation == "delete":
                await ctr.change("remove", uids=[uid])
            else:
                await ctr.change("update", uid=uid, name=item["summary"], checked=item["status"] == "completed")
        else:
            list_id = self.selection["keep_list_id"]
            if operation == "add":
                await keep.api.async_create_todo_item(list_id, item["summary"])
            elif operation == "delete":
                await keep.api.async_delete_todo_item(list_id, uid)
            else:
                await keep.api.async_update_todo_item(list_id, uid, item["summary"], item["status"] == "completed")
        # The engine performs a fresh raising remote read to push Keep mutations
        # and verify them. A local API method returning is not completion evidence.

    async def save(self, state):
        await self.store.async_save(self.record(state))
        self.state = state

    def record(self, state):
        return {"target": target_identity(self.selection), "engine": state,
                "last_success": self.last_success}

    @callback
    def schedule(self, reason):
        if self.closed:
            return
        if self.lock.locked() or (self.task and not self.task.done()):
            if reason in ("ctr_request", "keep_ha_change", "icloud_ha_change"):
                self.again = True
            return  # Own writes emit CTR change events: never feed them back.

        async def run():
            next_reason = reason
            while not self.closed:
                self.again = False
                await self.sync(next_reason)
                if not self.again:
                    break
                next_reason = "queued_request"
        self.task = self.entry.async_create_background_task(self.hass, run(), "CTR Keep sync")

    def publish(self):
        async_dispatcher_send(self.hass, f"{DOMAIN}_{self.entry.entry_id}")

    async def sync(self, reason):
        async with self.lock:
            if self.closed:
                return {"status": "not_confirmed", "error": "Keep synchronization is unloading"}
            self.status, self.last_reason, self.last_error = "syncing", reason, None
            previous_success = self.last_success
            self.changes = 0
            self.publish()
            try:
                async with asyncio.timeout(180):
                    engine = Engine(self.state, self.read, self.write, self.save)
                    self.changes = await engine.run()
                    final = await self.read()
                    # Confirm the entire mapping, including equal-count edits and
                    # additions that arrived during the run, rather than item counts alone.
                    for side in ("left", "right"):
                        expected = {row[side]: row["base"] for row in self.state["rows"]}
                        if final[side] != expected:
                            raise SyncBlocked("Lists changed during the final verification; another sync is needed")
                    self.status, self.last_success = "synced", utcnow().isoformat()
            except asyncio.CancelledError:
                self.status = "not_confirmed"
                self.last_error = "Synchronization canceled; pending additions will only be read back"
                self.publish()
                raise
            except Exception as err:
                self.status = "not_confirmed"
                self.last_error = str(err) if isinstance(err, SyncBlocked) else "A remote list operation failed or timed out"
                _LOGGER.warning("Shopping-list synchronization not confirmed (%s)", type(err).__name__)
            try:
                await self.store.async_save(self.record(self.state))
            except Exception:
                self.status, self.last_success = "not_confirmed", previous_success
                self.last_error = "Synchronization status could not be saved"
            self.publish()
            return {"status": self.status, "changes": self.changes,
                    "last_success": self.last_success, "error": self.last_error}
