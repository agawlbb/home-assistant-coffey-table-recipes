"""Asynchronous shopping client; never logs credentials or server responses."""
import asyncio
from copy import deepcopy
from uuid import uuid4
import aiohttp
from .const import API_URL


class ApiError(Exception):
    def __init__(self, status=0):
        super().__init__(f"CoffeyTableRecipes request failed ({status})")
        self.status = status


class Client:
    def __init__(self, session, token=None):
        self.session, self.token = session, token
        self._lock = asyncio.Lock()

    async def _http(self, method, url, **kwargs):
        try:
            async with self.session.request(method, url, timeout=aiohttp.ClientTimeout(total=20), allow_redirects=False, **kwargs) as response:
                if response.status >= 300:
                    raise ApiError(response.status)
                result = await response.json()
                if not isinstance(result, dict):
                    raise ApiError()
                return result
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise ApiError() from err

    async def request(self, method, path, *, token=None, body=None):
        headers = {"Authorization": f"Bearer {token or self.token}"} if token or self.token else {}
        return await self._http(method, API_URL + path, headers=headers, json=body)

    async def sign_in(self, email, password):
        config = await self.request("GET", "/home-assistant/auth-config")
        result = await self._http("POST", "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
                                  params={"key": config["api_key"]},
                                  json={"email": email, "password": password, "returnSecureToken": True})
        identity = result["idToken"]
        households = await self.request("GET", "/households", token=identity)
        return identity, households["households"]

    async def connect(self, identity, household):
        return await self.request("POST", f"/households/{household}/home-assistant-connections", token=identity, body={})

    async def read(self):
        result = await self.request("GET", "/home-assistant/shopping-list")
        if (not isinstance(result.get("revision"), int) or not isinstance(result.get("items"), list)
                or not isinstance(result.get("departments"), list)):
            raise ApiError()
        for item in result["items"]:
            if (not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("id", "name", "department"))
                    or not isinstance(item.get("checked"), bool)):
                raise ApiError()
        journal = result.get("sync_requests", {"latest_id": 0, "requests": []})
        if (not isinstance(journal, dict) or not isinstance(journal.get("latest_id"), int)
                or not isinstance(journal.get("requests"), list)):
            raise ApiError()
        for request in journal["requests"]:
            if (not isinstance(request, dict) or not isinstance(request.get("id"), int)
                    or not isinstance(request.get("requested_at"), str) or request.get("reason") != "shopping_list_opened"):
                raise ApiError()
        return result

    async def revoke(self):
        return await self.request("DELETE", "/home-assistant/connection")

    async def mutate(self, operation, *, uid=None, name=None, checked=None, uids=None):
        """Retry only rejected conflicts; never replay ambiguous network failures."""
        new_uid = uuid4().hex
        async with self._lock:
            for attempt in range(3):
                state = await self.read()
                items = deepcopy(state["items"])
                if operation == "add":
                    if not name or not name.strip() or len(name.strip()) > 200:
                        raise ApiError(422)
                    items.append({"id": new_uid, "name": name.strip(), "checked": False, "department": ""})
                elif operation == "remove":
                    if set(uids) - {item["id"] for item in items}:
                        raise ApiError(404)
                    items = [item for item in items if item["id"] not in uids]
                elif operation == "update":
                    matches = [item for item in items if item["id"] == uid]
                    if not matches:
                        raise ApiError(404)
                    if name is not None:
                        if not name.strip() or len(name.strip()) > 200:
                            raise ApiError(422)
                        matches[0]["name"] = name.strip()
                    if checked is not None:
                        matches[0]["checked"] = checked
                else:
                    raise ValueError("Unknown operation")
                try:
                    return await self.request("POST", "/home-assistant/shopping-list", body={"revision": state["revision"], "items": items})
                except ApiError as err:
                    if err.status != 409 or attempt == 2:
                        raise
