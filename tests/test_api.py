"""Transport-level client checks, without needing a running HA installation."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock

# Load just the client under an isolated package, leaving HA lifecycle untouched.
ROOT = Path(__file__).resolve().parents[1] / "custom_components/coffey_table_recipes"
pkg = types.ModuleType("ctr_client_test")
pkg.__path__ = [str(ROOT)]
sys.modules[pkg.__name__] = pkg
spec = importlib.util.spec_from_file_location("ctr_client_test.api", ROOT / "api.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


def snapshot(revision=1, items=None):
    return {"revision": revision, "items": items if items is not None else [
        {"id": "milk", "name": "Milk", "checked": False, "department": "Dairy"}], "departments": ["Dairy"]}


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_retries_conflict_preserving_other_member(self):
        client = api.Client(None, "household-token")
        first = snapshot()
        latest = snapshot(2, first["items"] + [{"id": "bread", "name": "Bread", "checked": True, "department": ""}])
        client.read = AsyncMock(side_effect=[first, latest])
        client.request = AsyncMock(side_effect=[api.ApiError(409), snapshot(3)])
        await client.mutate("add", name=" Eggs ")
        bodies = [call.kwargs["body"] for call in client.request.call_args_list]
        self.assertEqual(bodies[1]["revision"], 2)
        self.assertEqual([i["id"] for i in bodies[1]["items"][:-1]], ["milk", "bread"])
        self.assertEqual(bodies[0]["items"][-1]["id"], bodies[1]["items"][-1]["id"])
        self.assertEqual(bodies[1]["items"][-1]["name"], "Eggs")
        self.assertNotIn("departments", bodies[1])
        self.assertEqual(first, snapshot())

    async def test_check_uncheck_rename_preserves_department(self):
        client = api.Client(None)
        client.read = AsyncMock(return_value=snapshot())
        client.request = AsyncMock(return_value=snapshot(2))
        for checked in (True, False):
            await client.mutate("update", uid="milk", checked=checked, name="Whole milk")
            item = client.request.call_args.kwargs["body"]["items"][0]
            self.assertEqual(item, {"id": "milk", "name": "Whole milk", "checked": checked, "department": "Dairy"})

    async def test_delete_exact_uids(self):
        client = api.Client(None)
        client.read = AsyncMock(return_value=snapshot())
        client.request = AsyncMock(return_value=snapshot(2, []))
        await client.mutate("remove", uids=["milk"])
        self.assertEqual(client.request.call_args.kwargs["body"]["items"], [])
        with self.assertRaises(api.ApiError):
            await client.mutate("remove", uids=["missing"])

    async def test_network_failure_not_replayed(self):
        client = api.Client(None)
        client.read = AsyncMock(return_value=snapshot())
        client.request = AsyncMock(side_effect=api.ApiError())
        with self.assertRaises(api.ApiError):
            await client.mutate("add", name="Milk")
        self.assertEqual(client.request.await_count, 1)

    async def test_conflicts_are_bounded(self):
        client = api.Client(None)
        client.read = AsyncMock(return_value=snapshot())
        client.request = AsyncMock(side_effect=api.ApiError(409))
        with self.assertRaises(api.ApiError):
            await client.mutate("add", name="Milk")
        self.assertEqual(client.request.await_count, 3)

    async def test_missing_item_is_not_recreated(self):
        client = api.Client(None)
        client.read = AsyncMock(return_value=snapshot(items=[]))
        client.request = AsyncMock()
        with self.assertRaises(api.ApiError):
            await client.mutate("update", uid="milk", checked=True)
        client.request.assert_not_called()

    async def test_login_discards_personal_credentials(self):
        client = api.Client(None)
        client.request = AsyncMock(side_effect=[{"api_key":"fixture-public-key"}, {"households":[{"id":"1"}]}])
        client._http = AsyncMock(return_value={"idToken":"fixture-id", "refreshToken":"unused"})
        identity, households = await client.sign_in("fixture@example.com", "fixture-password")
        self.assertEqual(identity, "fixture-id")
        self.assertEqual(households, [{"id":"1"}])
        self.assertIsNone(client.token)
        self.assertFalse(hasattr(client, "password"))

    async def test_invalid_names_and_unknown_operations(self):
        client = api.Client(None)
        client.read = AsyncMock(return_value=snapshot())
        for name in ("", " ", "x" * 201):
            with self.assertRaises(api.ApiError):
                await client.mutate("add", name=name)
        with self.assertRaises(ValueError):
            await client.mutate("invalid")

    async def test_transport_preserves_status_without_response_data(self):
        class Response:
            status = 401
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def json(self): raise AssertionError("Error body must not be read or logged")
        class Session:
            def request(self, *args, **kwargs):
                self.kwargs = kwargs
                return Response()
        session = Session()
        client = api.Client(session, "private-fixture-token")
        with self.assertRaises(api.ApiError) as error:
            await client.request("DELETE", "/home-assistant/connection")
        self.assertEqual(error.exception.status, 401)
        self.assertNotIn("private-fixture-token", str(error.exception))
        self.assertEqual(session.kwargs["headers"]["Authorization"], "Bearer private-fixture-token")
        self.assertFalse(session.kwargs["allow_redirects"])

    async def test_rejects_invalid_server_snapshot(self):
        client = api.Client(None)
        for state in ({}, {"revision":1,"items":[{}],"departments":[]}):
            client.request = AsyncMock(return_value=state)
            with self.assertRaises(api.ApiError):
                await client.read()


if __name__ == "__main__":
    unittest.main()
