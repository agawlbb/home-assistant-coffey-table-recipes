"""Persistent household-scoped shopping transport. Credentials stay in frames."""
import asyncio
import json
import time
from uuid import uuid4
import aiohttp

URL = "wss://ctr.coffeybean.com/shopping"


class SocketError(Exception):
    def __init__(self, status=503):
        self.status = status
        super().__init__(f"Shopping synchronization failed ({status})")


class ShoppingSocket:
    def __init__(self, session, token, on_snapshot, url=URL):
        self.session, self.token, self.on_snapshot, self.url = session, token, on_snapshot, url
        self.household = None
        self.ready = asyncio.Event()
        self.closed = False
        self.task = None
        self.ws = None
        self.pending = {}
        self.server_time_ms = None
        self.received_at = None
        self.auth_error = None

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._run())

    async def _run(self):
        delay = 1
        while not self.closed:
            heartbeat = None
            try:
                async with self.session.ws_connect(self.url, heartbeat=25, timeout=aiohttp.ClientWSTimeout(ws_receive=65),
                                                   max_msg_size=5 * 1024 * 1024) as ws:
                    self.ws = ws
                    self.auth_error = None
                    self.ready.clear()
                    await ws.send_json({"v": 1, "type": "auth", "token": self.token})
                    heartbeat = asyncio.create_task(self._heartbeat(ws))
                    async for frame in ws:
                        if frame.type != aiohttp.WSMsgType.TEXT:
                            raise SocketError()
                        message = json.loads(frame.data)
                        if not isinstance(message, dict):
                            raise ValueError("Invalid shopping message")
                        self.server_time_ms = message.get("server_time_ms")
                        self.received_at = time.monotonic()
                        kind = message.get("type")
                        if kind in ("ready", "snapshot"):
                            household = message["household_id"]
                            if self.household is not None and household != self.household:
                                raise SocketError(403)
                            self.household = household
                            await self.on_snapshot(message["data"])
                            self.ready.set()
                            delay = 1
                        elif kind == "response":
                            future = self.pending.pop(message.get("id"), None)
                            if future is not None and not future.done():
                                future.set_result(message["data"])
                        elif kind == "error":
                            error = SocketError(message.get("status", 503))
                            future = self.pending.pop(message.get("id"), None)
                            if future is not None and not future.done():
                                future.set_exception(error)
                            if message.get("id") is None or error.status in (401, 403, 404):
                                self.auth_error = error
                                self.ready.set()
                                raise error
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, SocketError, OSError):
                pass
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()
                    try:
                        await heartbeat
                    except (asyncio.CancelledError, aiohttp.ClientError):
                        pass
                if self.auth_error is None:
                    self.ready.clear()
                self.ws = None
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(SocketError())
                self.pending.clear()
            if not self.closed:
                await asyncio.sleep(delay)
                delay = min(30, delay * 2)

    async def _heartbeat(self, ws):
        while True:
            await asyncio.sleep(25)
            await ws.send_json({"v": 1, "type": "ping"})

    async def request(self, body=None):
        self.start()
        try:
            await asyncio.wait_for(self.ready.wait(), 20)
            if self.auth_error is not None:
                raise self.auth_error
            uid = uuid4().hex
            future = asyncio.get_running_loop().create_future()
            self.pending[uid] = future
            try:
                await self.ws.send_json({"v": 1, "type": "request", "id": uid,
                                         "path": f"/households/{self.household}/shopping-list",
                                         **({"body": body} if body is not None else {})})
                return await asyncio.wait_for(future, 20)
            finally:
                self.pending.pop(uid, None)
        except (aiohttp.ClientError, asyncio.TimeoutError, AttributeError) as err:
            raise SocketError() from err

    async def close(self):
        self.closed = True
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

