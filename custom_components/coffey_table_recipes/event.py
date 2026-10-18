"""Discoverable event entity for shopping automations."""
from homeassistant.components.event import EventEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ShoppingEvents(entry.runtime_data, entry)])


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
