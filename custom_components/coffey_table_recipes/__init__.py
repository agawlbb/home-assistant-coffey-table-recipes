"""Household shopping lists for Home Assistant."""
import asyncio
from datetime import timedelta
import logging
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .api import ApiError, Client
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, EVENT_CHANGED, EVENT_SYNC_REQUESTED
from .keep_sync import async_register_keep_service, async_setup_keep_sync, async_stop_keep_sync, migrate_legacy_bridge

PLATFORMS = [Platform.TODO, Platform.EVENT, Platform.SENSOR]
_LOGGER = logging.getLogger(__name__)


class ShoppingCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)))
        self.entry = entry
        self.options = dict(entry.options)
        self.client = Client(async_get_clientsession(hass), entry.data["token"])
        self.last_event = None
        self.last_sync_requests = []
        self._snapshot_lock = asyncio.Lock()

    def accept(self, state):
        previous = self.data
        journal = state.get("sync_requests", {"latest_id": 0, "requests": []})
        cursor = self.entry.data.get("sync_request_cursor")
        self.last_sync_requests = []
        if cursor is not None:
            for request in journal["requests"]:
                if request["id"] > cursor:
                    payload = {
                        "household_id": self.entry.data["household_id"],
                        "config_entry_id": self.entry.entry_id,
                        "request_id": request["id"], "requested_at": request["requested_at"],
                        "reason": request["reason"],
                    }
                    self.last_sync_requests.append(payload)
                    self.hass.bus.async_fire(EVENT_SYNC_REQUESTED, payload)
        if cursor != journal["latest_id"]:
            self.hass.config_entries.async_update_entry(self.entry, data={
                **self.entry.data, "sync_request_cursor": journal["latest_id"],
            })
        if previous is not None and any(state[key] != previous[key] for key in ("revision", "items", "departments")):
            before = {item["id"]: item for item in previous["items"]}
            after = {item["id"]: item for item in state["items"]}
            payload = {"household_id": self.entry.data["household_id"], "config_entry_id": self.entry.entry_id,
                       "revision": state["revision"],
                       "added": [item for key, item in after.items() if key not in before],
                       "removed": [item for key, item in before.items() if key not in after],
                       "updated": [item for key, item in after.items() if key in before and item != before[key]],
                       "items": state["items"], "departments": state["departments"]}
            self.last_event = payload
            self.hass.bus.async_fire(EVENT_CHANGED, payload)
        return state

    async def _async_update_data(self):
        async with self._snapshot_lock:
            try:
                return self.accept(await self.client.read())
            except ApiError as err:
                if err.status == 401:
                    raise ConfigEntryAuthFailed("Reconnect your household") from err
                raise UpdateFailed("Cannot refresh the shopping list") from err

    async def change(self, operation, **kwargs):
        async with self._snapshot_lock:
            state = await self.client.mutate(operation, **kwargs)
            self.async_set_updated_data(self.accept(state))


async def async_setup_entry(hass, entry):
    await migrate_legacy_bridge(hass, entry)
    coordinator = ShoppingCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_setup_keep_sync(hass, entry)
    async_register_keep_service(hass)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass, entry):
    if entry.runtime_data.options != dict(entry.options):
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    await async_stop_keep_sync(entry)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass, entry):
    try:
        await Client(async_get_clientsession(hass), entry.data["token"]).revoke()
    except ApiError as err:
        if err.status != 401:
            _LOGGER.warning("Local connection removed; server revocation failed. Ask a household administrator to revoke it")
