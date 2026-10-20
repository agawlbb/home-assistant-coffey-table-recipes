"""Optional Apple Reminders synchronization through HA's iCloud integration."""
import asyncio
from datetime import date, datetime, timedelta
import hashlib

import voluptuous as vol
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import DOMAIN, EVENT_CHANGED, EVENT_SYNC_REQUESTED
from .keep_engine import SyncBlocked
from .keep_sync import KeepSync

CONF_ICLOUD_LIST = "icloud_list"
CONF_ICLOUD_TARGET = "icloud_target"


def resolve_icloud_selection(hass, entity_id, ctr_entry_id):
    entity = er.async_get(hass).async_get(entity_id)
    if not entity or entity.domain != "todo" or entity.platform != "icloud" or entity.disabled_by:
        raise SyncBlocked("Select an enabled Apple iCloud to-do list")
    owner = hass.config_entries.async_get_entry(entity.config_entry_id)
    if not owner or owner.domain != "icloud":
        raise SyncBlocked("The selected iCloud account is unavailable")
    target = {"icloud_entry": owner.entry_id, "icloud_unique": entity.unique_id}
    for other in hass.config_entries.async_entries(DOMAIN):
        if other.entry_id != ctr_entry_id and other.options.get(CONF_ICLOUD_TARGET) == target:
            raise SyncBlocked("This iCloud list is already synchronized by another CTR household")
    return target


class IcloudSync(KeepSync):
    """Reuse the durable three-way engine and status lifecycle, with separate storage."""

    def __init__(self, hass, entry, selection):
        # KeepSync's constructor expects Keep identity only to construct its Store.
        # Supply no fake identity: initialize the shared runtime here instead.
        self.hass, self.entry, self.selection = hass, entry, selection
        suffix = hashlib.sha256(f"{selection['icloud_entry']}:{selection['icloud_unique']}".encode()).hexdigest()[:16]
        self.store = Store(hass, 1, f"{DOMAIN}.icloud_sync.{entry.entry_id}.{suffix}")
        self.state = None
        self.lock = asyncio.Lock()
        self.task = None
        self.again = self.closed = False
        self.status = "not_confirmed"
        self.last_success = self.last_reason = self.last_error = None
        self.changes = 0
        self._reminders = {}
        self._writing = False
        self._refreshing = False

    def target(self):
        return {key: self.selection[key] for key in ("icloud_entry", "icloud_unique")}

    async def load(self):
        saved = await self.store.async_load()
        if saved is not None:
            if saved.get("target") != self.target():
                raise SyncBlocked("Saved item mapping belongs to a different iCloud list")
            self.state, self.last_success = saved["engine"], saved.get("last_success")

    def record(self, state):
        return {"target": self.target(), "engine": state, "last_success": self.last_success}

    def keep_entity_id(self):
        # Shared scheduler uses this accessor for the external list identity.
        return er.async_get(self.hass).async_get_entity_id("todo", "icloud", self.selection["icloud_unique"])

    def clients(self):
        if self.closed:
            raise SyncBlocked("iCloud synchronization is unloading")
        owner = self.hass.config_entries.async_get_entry(self.selection["icloud_entry"])
        if (self.entry.state.value not in ("loaded", "setup_in_progress") or not owner
                or owner.state.value != "loaded"):
            raise SyncBlocked("A shopping-list integration is not loaded")
        entity_id = self.keep_entity_id()
        entity = er.async_get(self.hass).async_get(entity_id) if entity_id else None
        if not entity or entity.config_entry_id != owner.entry_id or entity.disabled_by:
            raise SyncBlocked("The selected iCloud list identity is unavailable")
        coordinator = getattr(owner.runtime_data, "reminders_coordinator", None)
        if coordinator is None:
            raise SyncBlocked("Apple iCloud Reminders requires Home Assistant 2026.10 or later")
        prefix = f"{owner.unique_id}_"
        unique = self.selection["icloud_unique"]
        if not unique.startswith(prefix):
            raise SyncBlocked("The selected iCloud account identity has changed")
        return self.entry.runtime_data, coordinator, unique[len(prefix):]

    async def read(self):
        ctr, coordinator, list_id = self.clients()
        # async_refresh bypasses request-refresh's debounce and records failures.
        # A failed coordinator refresh can retain data: never use that cache.
        self._refreshing = True
        try:
            await coordinator.async_refresh()
        finally:
            self._refreshing = False
        if not coordinator.last_update_success:
            raise SyncBlocked("Could not fetch fresh iCloud reminders; no cached data was used")
        if getattr(coordinator, "_skipped_undecoded", False):
            raise SyncBlocked("Some iCloud reminders could not be decrypted; approve access on an Apple device")
        data = (coordinator.data or {}).get(list_id)
        if data is None:
            raise SyncBlocked("The selected iCloud list is missing")
        self._reminders = {item.uid: item for item in data.reminders}
        async with ctr._snapshot_lock:
            snapshot = await ctr.client.read()
            ctr.async_set_updated_data(ctr.accept(snapshot))
        result = {
            "left": {item["id"]: {"summary": item["name"], "status": "completed" if item["checked"] else "needs_action"}
                     for item in snapshot["items"]},
            "right": {uid: {"summary": item.summary, "status": "completed" if item.completed else "needs_action"}
                      for uid, item in self._reminders.items()},
        }
        if any(len(side) > 1000 or any(not item["summary"].strip() for item in side.values()) for side in result.values()):
            raise SyncBlocked("List exceeds 1000 items or contains blank reminders; sync stopped")
        return result

    async def write(self, side, operation, uid, item):
        ctr, _, _ = self.clients()
        if side == "left":
            if operation == "add":
                await ctr.change("add", name=item["summary"])
            elif operation == "delete":
                await ctr.change("remove", uids=[uid])
            else:
                await ctr.change("update", uid=uid, name=item["summary"], checked=item["status"] == "completed")
            return
        data = {"entity_id": self.keep_entity_id()}
        if operation == "add":
            service, data["item"] = "add_item", item["summary"]
        elif operation == "delete":
            service, data["item"] = "remove_item", uid
        else:
            service = "update_item"
            reminder = self._reminders[uid]
            data.update(item=uid, rename=item["summary"], status=item["status"], description=reminder.description or "")
            if isinstance(reminder.due, datetime):
                data["due_datetime"] = reminder.due.isoformat()
            elif isinstance(reminder.due, date):
                data["due_date"] = reminder.due.isoformat()
        self._writing = True
        try:
            await self.hass.services.async_call("todo", service, data, blocking=True)
        finally:
            self._writing = False


async def async_setup_icloud_sync(hass, entry):
    entry.runtime_data.icloud_sync = None
    target = entry.options.get(CONF_ICLOUD_TARGET)
    if not target:
        return
    sync = IcloudSync(hass, entry, target)
    await sync.load()
    entry.runtime_data.icloud_sync = sync

    @callback
    def ctr_event(event):
        if event.data.get("config_entry_id") == entry.entry_id:
            sync.schedule("ctr_request" if event.event_type == EVENT_SYNC_REQUESTED else "ctr_change")

    @callback
    def todo_event(event):
        if sync._writing or event.data.get("domain") != "todo" or event.data.get("service") == "get_items":
            return
        targets = event.data.get("service_data", {}).get("entity_id", [])
        if sync.keep_entity_id() in ([targets] if isinstance(targets, str) else targets):
            sync.schedule("icloud_ha_change")

    @callback
    def refreshed():
        # Observe completed HA edits (including renames with unchanged counts),
        # rather than relying only on the service-call event emitted before IO.
        if not sync._refreshing and not sync._writing:
            sync.schedule("icloud_ha_change")

    @callback
    def periodic(now):
        sync.schedule("poll")

    entry.async_on_unload(hass.bus.async_listen(EVENT_CHANGED, ctr_event))
    entry.async_on_unload(hass.bus.async_listen(EVENT_SYNC_REQUESTED, ctr_event))
    entry.async_on_unload(hass.bus.async_listen("call_service", todo_event))
    owner = hass.config_entries.async_get_entry(target["icloud_entry"])
    coordinator = getattr(getattr(owner, "runtime_data", None), "reminders_coordinator", None)
    if coordinator is not None:
        entry.async_on_unload(coordinator.async_add_listener(refreshed))
    entry.async_on_unload(async_track_time_interval(hass, periodic, timedelta(seconds=60)))
    sync.schedule("startup")


async def async_stop_icloud_sync(entry):
    sync = getattr(entry.runtime_data, "icloud_sync", None)
    if sync is not None:
        sync.closed = True
        if sync.task and not sync.task.done():
            sync.task.cancel()
            try:
                await sync.task
            except asyncio.CancelledError:
                pass


def async_register_icloud_service(hass):
    if hass.services.has_service(DOMAIN, "sync_icloud"):
        return

    async def sync_service(call):
        configured = [entry for entry in hass.config_entries.async_entries(DOMAIN)
                      if entry.state.value == "loaded" and getattr(entry.runtime_data, "icloud_sync", None)]
        entry_id = call.data.get("config_entry_id")
        selected = next((e for e in configured if e.entry_id == entry_id), None) if entry_id else (
            configured[0] if len(configured) == 1 else None)
        if selected is None:
            raise HomeAssistantError("Select one CTR household with iCloud synchronization enabled")
        result = await selected.runtime_data.icloud_sync.sync("manual")
        if result["status"] != "synced":
            raise HomeAssistantError(result["error"])
        return result

    hass.services.async_register(DOMAIN, "sync_icloud", sync_service,
        schema=vol.Schema({vol.Optional("config_entry_id"): str}), supports_response=SupportsResponse.OPTIONAL)
