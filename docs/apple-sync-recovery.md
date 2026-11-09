# Apple sync recovery and visible failures

Task `fix/apple-sync-recovery`, based on fetched `origin/main` at
`780d6c5`.

On November 9, Apple sync reported an unconfirmed earlier addition and stopped
reconciling subsequent list changes. The previous empty-list recovery only
worked when both entire lists were empty. The owner confirmed that only Protein
drinks should remain; the live Apple list was reconciled and read back with that
single item. CTR also contained that single item. Manual sync still reported the
stored unconfirmed addition, so matching contents alone did not prove recovery.

The engine now retires an unconfirmed addition when a successful fresh read of
both lists finds neither its source nor a possible created counterpart. Other
items can then continue syncing. If the counterpart exists, recovery establishes
its original value so a source deletion propagates instead of resurrecting the
removed item. Missing additions whose source still exists, ambiguous duplicates,
and failed reads retain the existing protection against uncertain writes.

With owner approval, failed Apple/Keep sync now creates a Home Assistant
persistent notification naming the external service and explaining the error.
Repeated failures update the same notification; successful verified sync clears
it. Existing one-minute automatic retries remain in place. This is a Home
Assistant notification, not a native CTR app notification. The reconciliation
engine and notification handler are shared with Google Keep.

No Flutter changes or app builds are needed. Source publication does not install
this change in the running Home Assistant instance.

Validation: all 20 focused engine tests and 11 mocked adapter tests passed.
The 14 Home Assistant lifecycle tests in `test_icloud_native.py` and
`test_keep_native.py` passed, including reading the actual notification through
HA's WebSocket API, repeated-failure deduplication, and clearing after recovery.
The native tests required execution outside the restricted sandbox; the
sandboxed attempt stalled and was stopped. `git diff --check` passed. No full
suite, app build, main integration, or Home Assistant installation ran.

The push workflow normally runs the complete integration suite, so the task
commit uses `[skip ci]` to preserve the owner's focused-validation requirement.
