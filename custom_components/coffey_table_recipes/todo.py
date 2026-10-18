"""Native To-do list support."""
from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .api import ApiError
from .const import DOMAIN

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ShoppingList(entry.runtime_data, entry)])


class ShoppingList(CoordinatorEntity, TodoListEntity):
    _attr_has_entity_name = True
    _attr_name = "Shopping list"
    _attr_icon = "mdi:cart-outline"
    _attr_supported_features = (TodoListEntityFeature.CREATE_TODO_ITEM | TodoListEntityFeature.DELETE_TODO_ITEM |
                               TodoListEntityFeature.UPDATE_TODO_ITEM)

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data['household_id']}_shopping_list"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.data["household_id"])},
                                  "name": entry.title, "manufacturer": "CoffeyTableRecipes",
                                  "entry_type": "service", "configuration_url": "https://coffeybean.com/CoffeyTableRecipes/app/"}

    @property
    def todo_items(self):
        return [TodoItem(uid=item["id"], summary=item["name"],
                         status=TodoItemStatus.COMPLETED if item["checked"] else TodoItemStatus.NEEDS_ACTION,
                         description=item["department"] or None) for item in self.coordinator.data["items"]]

    @property
    def extra_state_attributes(self):
        return {"household_id": self.coordinator.entry.data["household_id"], "revision": self.coordinator.data["revision"],
                "departments": self.coordinator.data["departments"]}

    async def _change(self, operation, **kwargs):
        try:
            await self.coordinator.change(operation, **kwargs)
        except ApiError as err:
            if err.status == 401:
                self.coordinator.entry.async_start_reauth(self.hass)
            raise HomeAssistantError("Could not save the shopping list. Refresh and try again.") from err

    async def async_create_todo_item(self, item):
        await self._change("add", name=item.summary)

    async def async_update_todo_item(self, item):
        await self._change("update", uid=item.uid, name=item.summary,
                           checked=None if item.status is None else item.status == TodoItemStatus.COMPLETED)

    async def async_delete_todo_items(self, uids):
        await self._change("remove", uids=uids)
