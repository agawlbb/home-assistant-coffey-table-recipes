"""Asynchronous shopping client; never logs credentials or server responses."""
import asyncio
import time
from copy import deepcopy
from uuid import uuid4
import aiohttp
from .const import API_URL
from .shopping_socket import ShoppingSocket, SocketError


class ApiError(Exception):
    def __init__(self, status=0):
        super().__init__(f"CoffeyTableRecipes request failed ({status})")
        self.status = status


class Client:
    def __init__(self, session, token=None, *, store=None, on_change=None):
        self.session, self.token = session, token
        self._lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._sync_lock = asyncio.Lock()
        self.store, self.on_change = store, on_change
        self._state = None
        self._queue = []
        self._loaded = False
        self._load_lock = asyncio.Lock()
        self._last_edit_ms = 0
        self._drain_task = None
        self.socket = ShoppingSocket(session, token, self._receive) if token else None

    async def load(self):
        async with self._load_lock:
            if self._loaded:
                return
            saved = await self.store.async_load() if self.store else None
            if saved:
                self._state, self._queue = saved["state"], saved["queue"]
                self._last_edit_ms = saved.get("last_edit_ms", 0)
            self._loaded = True

    async def _persist(self):
        if self.store:
            await self.store.async_save({"state": self._state, "queue": self._queue, "last_edit_ms": self._last_edit_ms})

    def local_state(self):
        if self._state is None:
            return None
        result = deepcopy(self._state)
        for body in self._queue:
            before = {i["id"]: i for i in body["base_items"]}
            after = {i["id"]: i for i in body["items"]}
            result["items"] = [i for i in result["items"] if i["id"] not in before or i["id"] in after]
            rows = {i["id"]: i for i in result["items"]}
            for uid, wanted in after.items():
                if uid not in before:
                    if uid not in rows:
                        result["items"].append(deepcopy(wanted))
                elif uid in rows:
                    for field in ("name", "checked", "department", "note"):
                        if field in wanted and wanted[field] != before[uid].get(field):
                            rows[uid][field] = wanted[field]
        return result

    def _schedule_drain(self):
        if self._queue and (self._drain_task is None or self._drain_task.done()):
            self._drain_task = asyncio.create_task(self._background_drain())

    async def _background_drain(self):
        try:
            await self._drain()
        except (ApiError, OSError):
            pass  # Durable operations are retried after reconnect or the next read.

    async def _receive(self, state):
        try:
            self._validated(state)
        except ApiError as err:
            raise ValueError("Invalid shopping snapshot") from err
        await self.load()
        async with self._state_lock:
            if not self._state or state["revision"] >= self._state["revision"]:
                previous = self._state
                self._state = deepcopy(state)
                try:
                    await self._persist()
                except Exception:
                    self._state = previous
                    raise
        if self.on_change:
            self.on_change(self.local_state())
        self._schedule_drain()

    async def _drain(self):
        async with self._sync_lock:
            while self._queue:
                body = self._queue[0]
                try:
                    result = self._validated(await self.socket.request(body))
                except SocketError as err:
                    raise ApiError(err.status) from err
                if result.get("acknowledged_change_id") != body["client_change_id"]:
                    raise ApiError(503)
                async with self._state_lock:
                    confirmed = self._queue.pop(0)
                    previous = self._state
                    if not self._state or result["revision"] >= self._state["revision"]:
                        self._state = deepcopy(result)
                    try:
                        await self._persist()
                    except Exception:
                        self._queue.insert(0, confirmed)
                        self._state = previous
                        raise
                if self.on_change:
                    self.on_change(self.local_state())
            return self._state

    async def close(self):
        if self._drain_task:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except (asyncio.CancelledError, ApiError, OSError):
                pass
        if self.socket:
            await self.socket.close()

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
        if path == "/home-assistant/shopping-list":
            await self.load()
            if self.socket is None:
                raise ApiError(401)
            try:
                if method == "GET":
                    if self._queue:
                        await self._drain()
                    result = await self.socket.request()
                    await self._receive(result)
                    return result
                if method != "POST" or body is None:
                    raise ApiError(405)
                # Persist the original payload before attempting delivery. Retries
                # after lost responses or HA restarts use exactly this identity.
                async with self._state_lock:
                    if "client_change_id" not in body:
                        if self._state is None:
                            raise ApiError(503)
                        elapsed = (time.monotonic() - self.socket.received_at) * 1000 if self.socket.received_at else 0
                        self._last_edit_ms = max(self._last_edit_ms + 1, int((self.socket.server_time_ms or time.time()*1000) + elapsed))
                        body = {**body, "base_items": deepcopy(body.get("base_items", self._state["items"])),
                                "client_change_id": uuid4().hex,
                                "edited_at_ms": self._last_edit_ms}
                    if not any(q["client_change_id"] == body["client_change_id"] for q in self._queue):
                        self._queue.append(deepcopy(body))
                        try:
                            await self._persist()
                        except Exception:
                            self._queue.pop()
                            raise
                result = self.local_state()
                self._schedule_drain()
                return result
            except SocketError as err:
                raise ApiError(err.status) from err
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
        return self._validated(await self.request("GET", "/home-assistant/shopping-list"))

    @staticmethod
    def _validated(result):
        if not isinstance(result, dict):
            raise ApiError()
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
        """Prepare changes from confirmed state; durable receipts make delivery replayable."""
        new_uid = uuid4().hex
        async with self._lock:
            for attempt in range(3):
                await self.load()
                state = self.local_state() or await self.read()
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
                    return await self.request("POST", "/home-assistant/shopping-list", body={"revision": state["revision"], "items": items, "base_items": deepcopy(state["items"])})
                except ApiError as err:
                    if err.status != 409 or attempt == 2:
                        raise
