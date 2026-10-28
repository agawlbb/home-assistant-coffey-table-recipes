"""Durable HA outbox and real WebSocket transport regressions."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from aiohttp import ClientSession, web
from test_api import api, snapshot

try:
    import pytest
    pytestmark = pytest.mark.usefixtures('socket_enabled')
except ImportError:
    pass  # These checks also run with standard-library unittest.


class Store:
    def __init__(self, value):
        self.value = deepcopy(value)
        self.fail = False

    async def async_load(self):
        return deepcopy(self.value)

    async def async_save(self, value):
        if self.fail:
            raise OSError("Device storage unavailable")
        self.value = deepcopy(value)


class ShoppingSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_delete_survives_restart_and_replays_original_receipt(self):
        store = Store({"state": snapshot(), "queue": []})
        first = api.Client(None, "fixture", store=store)
        first.socket = SimpleNamespace(request=AsyncMock(side_effect=api.SocketError()),
                                       server_time_ms=None, received_at=None, close=AsyncMock())
        result = await first.mutate("remove", uids=["milk"])
        self.assertEqual(result["items"], [])
        await first._drain_task
        original = deepcopy(store.value["queue"][0])
        await first.close()
        second = api.Client(None, "fixture", store=store)
        await second.load()
        self.assertEqual(second.local_state()["items"], [])
        second.socket = SimpleNamespace(request=AsyncMock(return_value={
            **snapshot(2, []), "acknowledged_change_id": original["client_change_id"]}), close=AsyncMock())
        await second._drain()
        self.assertEqual(second.socket.request.call_args.args[0], original)
        self.assertEqual(store.value["queue"], [])
        await second.close()

    async def test_storage_failure_does_not_accept_or_send_an_edit(self):
        store = Store({"state": snapshot(), "queue": []})
        client = api.Client(None, "fixture", store=store)
        client.socket = SimpleNamespace(request=AsyncMock(), server_time_ms=None, received_at=None, close=AsyncMock())
        await client.load()
        store.fail = True
        with self.assertRaises(OSError):
            await client.mutate("remove", uids=["milk"])
        self.assertEqual(client.local_state()["items"], snapshot()["items"])
        client.socket.request.assert_not_called()
        await client.close()

    async def test_push_preserves_offline_deletion_and_unrelated_addition(self):
        store = Store({"state": snapshot(), "queue": []})
        client = api.Client(None, "fixture", store=store)
        client.socket = SimpleNamespace(request=AsyncMock(side_effect=api.SocketError()),
                                       server_time_ms=None, received_at=None, close=AsyncMock())
        await client.mutate("remove", uids=["milk"])
        await client._drain_task
        bread = {"id": "bread", "name": "Bread", "checked": False, "department": ""}
        await client._receive(snapshot(2, snapshot()["items"] + [bread]))
        self.assertEqual(client.local_state()["items"], [bread])
        self.assertEqual(len(store.value["queue"]), 1)
        await client.close()

    async def test_transport_authenticates_and_delivers_push_without_polling(self):
        pushed = asyncio.Event()
        peer = asyncio.get_running_loop().create_future()
        async def handler(request):
            self.assertNotIn("token", request.query)
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            auth = await ws.receive_json()
            self.assertEqual(auth, {"v": 1, "type": "auth", "token": "fixture"})
            peer.set_result(ws)
            await ws.send_json({"type": "ready", "household_id": "1", "data": snapshot()})
            async for frame in ws:
                import json
                message = json.loads(frame.data)
                if message["type"] == "request":
                    await ws.send_json({"type": "response", "id": message["id"], "data": snapshot()})
            return ws
        app = web.Application()
        app.router.add_get("/shopping", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        async with ClientSession() as session:
            async def receive(state):
                if state["revision"] == 2:
                    pushed.set()
            socket = api.ShoppingSocket(session, "fixture", receive, url=f"http://127.0.0.1:{port}/shopping")
            try:
                self.assertEqual((await socket.request())["revision"], 1)
                await (await peer).send_json({"type": "snapshot", "household_id": "1", "data": snapshot(2, [])})
                await asyncio.wait_for(pushed.wait(), 2)
                self.assertTrue(socket.ready.is_set())
            finally:
                await socket.close()
        await runner.cleanup()


if __name__ == "__main__":
    unittest.main()
