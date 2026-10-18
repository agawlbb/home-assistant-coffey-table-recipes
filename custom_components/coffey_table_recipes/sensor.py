"""Evidence of the optional household Google Keep synchronization."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    # The sensor reads runtime_data dynamically; Keep setup runs after platforms.
    async_add_entities([KeepSyncStatus(entry)])


class KeepSyncStatus(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Google Keep synchronization"
    _attr_icon = "mdi:sync"

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = f"{entry.data['household_id']}_keep_sync_status"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.data["household_id"])}}

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass,
            f"{DOMAIN}_{self.entry.entry_id}", self.async_write_ha_state))

    @property
    def native_value(self):
        sync = getattr(self.entry.runtime_data, "keep_sync", None)
        return sync.status if sync else "disabled"

    @property
    def extra_state_attributes(self):
        sync = getattr(self.entry.runtime_data, "keep_sync", None)
        if sync is None:
            return {"last_success": None, "last_error": None}
        return {"last_success": sync.last_success, "last_reason": sync.last_reason,
                "last_error": sync.last_error, "changes": sync.changes,
                "mapped_items": len((sync.state or {}).get("rows", [])),
                "migrated_from": sync.migrated_from, "keep_entity_id": sync.keep_entity_id()}
