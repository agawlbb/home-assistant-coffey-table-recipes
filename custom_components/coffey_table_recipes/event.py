"""Discoverable event entity for shopping automations."""
from homeassistant.components.event import EventEntity
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, EVENT_SYNC_REQUESTED


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ShoppingEvents(entry.runtime_data, entry), ShoppingSyncEvents(entry.runtime_data, entry)])


class ShoppingEvents(CoordinatorEntity, EventEntity):
    _attr_has_entity_name = True
    _attr_name = "Shopping list changes"
    _attr_event_types = ["changed"]
    _attr_icon = "mdi:cart-arrow-down"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data['household_id']}_shopping_changes"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.data["household_id"])}}
        self._seen = coordinator.last_event

    def _handle_coordinator_update(self):
        payload = self.coordinator.last_event
        if payload is not None and payload is not self._seen:
            self._seen = payload
            self._trigger_event("changed", payload)
        self.async_write_ha_state()


class ShoppingSyncEvents(CoordinatorEntity, EventEntity):
    """Requests to synchronize an external list, independently of item changes."""
    _attr_has_entity_name = True
    _attr_name = "Shopping list synchronization"
    _attr_event_types = ["requested"]
    _attr_icon = "mdi:sync"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.data['household_id']}_shopping_sync"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.data["household_id"])}}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_SYNC_REQUESTED, self._sync_requested))
        for payload in self.coordinator.last_sync_requests:
            self._trigger_event("requested", payload)
            self.async_write_ha_state()

    @callback
    def _sync_requested(self, event):
        if event.data["config_entry_id"] == self._entry_id:
            self._trigger_event("requested", dict(event.data))
            self.async_write_ha_state()
