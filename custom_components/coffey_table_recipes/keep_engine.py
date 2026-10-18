"""Persistent item identity and three-way reconciliation, independent of HA."""
from copy import deepcopy

FIELDS = ("summary", "status")


class SyncBlocked(Exception):
    """Cannot safely infer whether a write completed."""


def value(item):
    return {key: item[key] for key in FIELDS}


def desired(base, left, right):
    """Merge fields; CTR wins a simultaneous same-field conflict.

    Deletion propagates only against an unchanged peer. An edit concurrent
    with deletion survives by recreating the deleted counterpart.
    """
    if left is None or right is None:
        remaining = left if left is not None else right
        if remaining is None or (base is not None and remaining == base):
            return None
        return remaining
    if base is None:
        return {"summary": left["summary"], "status": (
            "needs_action" if "needs_action" in (left["status"], right["status"])
            else "completed")}
    return {key: right[key] if left[key] == base[key] else left[key] for key in FIELDS}


class Engine:
    """All IO is injected. Save intent before creates, verify before commit."""

    def __init__(self, state, read, write, save):
        self.state = state if state is not None else {"rows": []}
        self.read = read
        self.write = write
        self.save = save

    async def persist(self):
        await self.save(deepcopy(self.state))

    async def recover(self, row, snapshots):
        """An uncertain create is never blindly replayed, including after restart."""
        pending = row.get("pending")
        if not pending:
            return snapshots
        side = pending["side"]
        candidates = [uid for uid, item in snapshots[side].items()
                      if uid not in pending["before"]
                      and item["summary"] == pending["desired"]["summary"]]
        known = pending.get("uid")
        if known is not None:
            candidates = [known] if known in snapshots[side] else []
        if len(candidates) != 1:
            raise SyncBlocked("An earlier add has an uncertain outcome; no add was replayed")
        uid = candidates[0]
        pending["uid"] = uid
        await self.persist()
        target = pending["desired"]
        current = snapshots[side][uid]
        if current["summary"] != target["summary"]:
            raise SyncBlocked("A pending addition was edited before it could be verified")
        if current["status"] != target["status"]:
            await self.write(side, "update", uid, target)
            snapshots = await self.read()
        if snapshots[side].get(uid) != target:
            raise SyncBlocked("An addition could not be verified")
        row[side] = uid
        row.pop("pending")
        await self.persist()
        return snapshots

    async def run(self):
        snapshots = await self.read()  # Must be fresh, available, complete on BOTH sides.
        for row in self.state["rows"]:
            snapshots = await self.recover(row, snapshots)

        # Pair newly encountered items one-to-one, preserving duplicate counts.
        used = {side: {r[side] for r in self.state["rows"] if r.get(side)}
                for side in ("left", "right")}
        unmatched = {side: sorted(set(snapshots[side]) - used[side]) for side in used}
        while unmatched["left"] or unmatched["right"]:
            left = unmatched["left"].pop(0) if unmatched["left"] else None
            right = None
            if left:
                name = snapshots["left"][left]["summary"].strip().casefold()
                right = next((uid for uid in unmatched["right"]
                              if snapshots["right"][uid]["summary"].strip().casefold() == name), None)
            elif unmatched["right"]:
                right = unmatched["right"][0]
            if right:
                unmatched["right"].remove(right)
            self.state["rows"].append({"left": left, "right": right, "base": None})
        await self.persist()

        changes = 0
        for row in list(self.state["rows"]):
            observed = {side: snapshots[side].get(row.get(side)) for side in used}
            target = desired(row["base"], observed["left"], observed["right"])
            for side in ("left", "right"):
                if observed[side] == target:
                    continue
                # Re-read before each write. Never overwrite a change since planning.
                fresh = await self.read()
                if any(fresh[s].get(row.get(s)) != observed[s] for s in used):
                    raise SyncBlocked("An item changed during synchronization; a fresh sync is needed")
                uid = row.get(side)
                if target is None:
                    await self.write(side, "delete", uid, None)
                elif observed[side] is None:
                    row["pending"] = {"side": side, "before": list(fresh[side]),
                                      "desired": deepcopy(target)}
                    await self.persist()
                    # If the process stops here, recovery only reads. An absent add
                    # requires explicit user reconciliation rather than risking a duplicate.
                    await self.write(side, "add", None, target)
                else:
                    await self.write(side, "update", uid, target)
                snapshots = await self.read()
                snapshots = await self.recover(row, snapshots)
                if snapshots[side].get(row.get(side)) != target:
                    raise SyncBlocked("The requested list change was not confirmed")
                observed[side] = target
                changes += 1
                if changes >= 100:
                    raise SyncBlocked("Reached the per-run change limit; request another sync")
            # Check both sides after the writes; a request/ack alone never advances base.
            if any(snapshots[s].get(row.get(s)) != target for s in used):
                raise SyncBlocked("The lists changed during synchronization")
            if target is None:
                self.state["rows"].remove(row)
            else:
                row["base"] = deepcopy(target)
            await self.persist()
        return changes
