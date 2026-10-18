import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("engine", Path(__file__).parents[1] / "custom_components/coffey_table_recipes/keep_engine.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Engine, SyncBlocked = module.Engine, module.SyncBlocked


def item(name="Milk", status="needs_action"):
    return {"summary": name, "status": status}


class Remote:
    def __init__(self, left=None, right=None):
        self.data = {"left": left or {}, "right": right or {}}
        self.saved = None
        self.calls = []
        self.failure = None
        self.fail_read = False
        self.counter = 0
        self.before_write_read = None

    async def read(self):
        if self.fail_read:
            raise ConnectionError("offline")
        if self.before_write_read:
            callback, self.before_write_read = self.before_write_read, None
            callback(self.data)
        return deepcopy(self.data)

    async def save(self, state):
        self.saved = deepcopy(state)

    async def write(self, side, operation, uid, value):
        self.calls.append((side, operation, uid, value))
        if self.failure == "before":
            raise ConnectionError("uncertain")
        if operation == "add":
            self.counter += 1
            self.data[side][f"new{self.counter}"] = item(value["summary"])
        elif operation == "delete":
            self.data[side].pop(uid, None)
        else:
            self.data[side][uid] = deepcopy(value)
        if self.failure == "after":
            raise ConnectionError("lost acknowledgment")

    async def run(self):
        return await Engine(deepcopy(self.saved), self.read, self.write, self.save).run()


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_union_then_no_feedback_loop(self):
        r = Remote({"c1": item("bread")}, {"k1": item("Protein drinks"), "k2": item("Brown sugar")})
        await r.run()
        self.assertEqual(sorted(v["summary"] for v in r.data["left"].values()), ["Brown sugar", "Protein drinks", "bread"])
        count = len(r.calls)
        self.assertEqual(await r.run(), 0)
        self.assertEqual(len(r.calls), count)

    async def test_checks_and_unchecks_both_directions(self):
        r = Remote({"c": item()}, {"k": item()})
        await r.run()
        for side, uid, status in [("left", "c", "completed"), ("right", "k", "needs_action"),
                                  ("right", "k", "completed"), ("left", "c", "needs_action")]:
            r.data[side][uid]["status"] = status
            await r.run()
            self.assertEqual(r.data["left"]["c"]["status"], status)
            self.assertEqual(r.data["right"]["k"]["status"], status)

    async def test_renames_both_directions_preserve_ids(self):
        r = Remote({"c": item()}, {"k": item()})
        await r.run()
        for side, uid, name in [("right", "k", "Oat milk"), ("left", "c", "Whole milk")]:
            r.data[side][uid]["summary"] = name
            await r.run()
            self.assertEqual(r.data["left"]["c"], item(name))
            self.assertEqual(r.data["right"]["k"], item(name))

    async def test_deletions_both_directions(self):
        for deleted in ("left", "right"):
            r = Remote({"c": item()}, {"k": item()})
            await r.run()
            r.data[deleted].clear()
            await r.run()
            self.assertEqual(r.data, {"left": {}, "right": {}})
            self.assertEqual(r.saved["rows"], [])

    async def test_duplicate_names_remain_distinct(self):
        r = Remote({"c1": item(), "c2": item()}, {"k": item()})
        await r.run()
        self.assertEqual(len(r.data["right"]), 2)
        r.data["left"]["c2"]["status"] = "completed"
        await r.run()
        self.assertEqual(sum(v["status"] == "completed" for v in r.data["right"].values()), 1)

    async def test_new_completed_item_stays_completed(self):
        for side in ("left", "right"):
            r = Remote()
            r.data[side]["a"] = item("Eggs", "completed")
            await r.run()
            for contents in r.data.values():
                self.assertEqual(list(contents.values()), [item("Eggs", "completed")])

    async def test_initial_case_pair_unchecked_survives(self):
        r = Remote({"c": item("Milk", "completed")}, {"k": item("milk")})
        await r.run()
        self.assertEqual(r.data["left"]["c"], item("Milk"))
        self.assertEqual(r.data["right"]["k"], item("Milk"))

    async def test_independent_field_edits_merge(self):
        r = Remote({"c": item()}, {"k": item()})
        await r.run()
        r.data["left"]["c"]["summary"] = "Oat milk"
        r.data["right"]["k"]["status"] = "completed"
        await r.run()
        self.assertEqual(r.data["right"]["k"], item("Oat milk", "completed"))
        self.assertEqual(r.data["left"]["c"], r.data["right"]["k"])

    async def test_same_field_conflict_ctr_wins(self):
        r = Remote({"c": item()}, {"k": item()})
        await r.run()
        r.data["left"]["c"]["summary"] = "A"
        r.data["right"]["k"]["summary"] = "B"
        await r.run()
        self.assertEqual(r.data["right"]["k"]["summary"], "A")

    async def test_concurrent_edit_survives_deletion(self):
        for side in ("left", "right"):
            r = Remote({"c": item()}, {"k": item()})
            await r.run()
            r.data[side].clear()
            other = "right" if side == "left" else "left"
            next(iter(r.data[other].values()))["summary"] = "Edited"
            await r.run()
            self.assertEqual(list(r.data[side].values()), [item("Edited")])

    async def test_offline_never_means_empty_or_deleted(self):
        r = Remote({"c": item()}, {"k": item()})
        await r.run()
        old = deepcopy(r.saved)
        r.fail_read = True
        with self.assertRaises(ConnectionError):
            await r.run()
        self.assertEqual(r.calls, [])
        self.assertEqual(r.saved, old)

    async def test_lost_create_ack_recovered_across_restart_without_replay(self):
        r = Remote({"c": item()})
        r.failure = "after"
        with self.assertRaises(ConnectionError):
            await r.run()
        self.assertIn("pending", r.saved["rows"][0])
        r.failure = None
        await r.run()
        self.assertEqual(len([c for c in r.calls if c[1] == "add"]), 1)
        self.assertEqual(len(r.data["right"]), 1)

    async def test_uncertain_missing_add_does_not_retry(self):
        r = Remote({"c": item()})
        r.failure = "before"
        with self.assertRaises(ConnectionError):
            await r.run()
        r.failure = None
        with self.assertRaises(SyncBlocked):
            await r.run()
        self.assertEqual(len(r.calls), 1)

    async def test_ambiguous_duplicate_add_recovery_blocks(self):
        r = Remote({"c": item()})
        r.failure = "after"
        with self.assertRaises(ConnectionError):
            await r.run()
        r.failure = None
        r.data["right"]["another"] = item()
        with self.assertRaises(SyncBlocked):
            await r.run()
        self.assertEqual(len(r.calls), 1)

    async def test_ack_without_effect_not_confirmed(self):
        r = Remote({"c": item()}, {"k": item()})
        await r.run()
        r.data["left"]["c"]["status"] = "completed"
        async def ignore(*args):
            pass
        with self.assertRaises(SyncBlocked):
            await Engine(r.saved, r.read, ignore, r.save).run()
        self.assertEqual(r.saved["rows"][0]["base"], item())

    async def test_read_race_does_not_overwrite_newer_edit(self):
        r = Remote({"c": item()}, {"k": item()})
        await r.run()
        r.data["left"]["c"]["summary"] = "Oat milk"
        reads = 0
        original = r.read
        async def race():
            nonlocal reads
            reads += 1
            if reads == 2:
                r.data["right"]["k"]["summary"] = "Newer"
            return await original()
        with self.assertRaises(SyncBlocked):
            await Engine(r.saved, race, r.write, r.save).run()
        self.assertEqual(r.data["right"]["k"]["summary"], "Newer")
        self.assertEqual(r.calls, [])


if __name__ == "__main__":
    unittest.main()
