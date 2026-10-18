"""Sign in, choose a household, and configure refresh timing in the UI."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode, TextSelector, TextSelectorConfig, TextSelectorType
from .api import ApiError, Client
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._identity = None
        self._households = []
        self._reauth_entry = None

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                client = Client(async_get_clientsession(self.hass))
                self._identity, households = await client.sign_in(user_input["email"], user_input["password"])
                self._households = [h for h in households if h["role"] in ("owner", "admin")]
                if self._reauth_entry:
                    self._households = [h for h in self._households if h["id"] == self._reauth_entry.data["household_id"]]
                if not self._households:
                    errors["base"] = "no_household"
                elif len(self._households) == 1:
                    return await self.async_step_household({"household_id": self._households[0]["id"]})
                else:
                    return await self.async_step_household()
            except ApiError as err:
                errors["base"] = "invalid_auth" if err.status in (400, 401) else "cannot_connect"
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("email"): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
            vol.Required("password"): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        }), errors=errors)

    async def async_step_household(self, user_input=None):
        errors = {}
        if user_input is not None:
            household = next((h for h in self._households if h["id"] == user_input["household_id"]), None)
            if household is None:
                return self.async_abort(reason="unknown_household")
            await self.async_set_unique_id(household["id"])
            if not self._reauth_entry:
                self._abort_if_unique_id_configured()
            try:
                client = Client(async_get_clientsession(self.hass))
                connection = await client.connect(self._identity, household["id"])
                client.token = connection["token"]
                try:
                    await client.read()
                except ApiError:
                    await client.revoke()
                    raise
                data = {"household_id": household["id"], "token": connection["token"]}
                self._identity = None
                if self._reauth_entry:
                    old = Client(async_get_clientsession(self.hass), self._reauth_entry.data["token"])
                    try:
                        await old.revoke()
                    except ApiError:
                        pass
                    return self.async_update_reload_and_abort(self._reauth_entry, data_updates=data)
                return self.async_create_entry(title=household["name"], data=data)
            except ApiError as err:
                if err.status in (401, 403):
                    return self.async_abort(reason="sign_in_again")
                self._identity = None
                return self.async_abort(reason="cannot_connect")
        return self.async_show_form(step_id="household", data_schema=vol.Schema({
            vol.Required("household_id"): SelectSelector(SelectSelectorConfig(
                options=[{"value": h["id"], "label": h["name"]} for h in self._households], mode=SelectSelectorMode.DROPDOWN))
        }), errors=errors)

    async def async_step_reauth(self, entry_data):
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_user()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow()


class OptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required("scan_interval", default=self.config_entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)):
                vol.All(vol.Coerce(int), vol.Range(min=5, max=300))
        }))
