# Apple empty-list recovery

Task: `fix/apple-empty-list-recovery`, based on fetched `origin/main`
`b9c199e99c49112bb1cc73dce1486604a55b035d`.

The live Apple sync reported an earlier addition with an uncertain outcome.
The shared reconciliation engine attempted recovery before processing any later
changes, so that addition could indefinitely block a cleared list. Historical
iCloud logs also reported authentication failures. A live removal initially
returned Apple's 503 TRY_AGAIN_LATER response after partially completing;
reading the remaining items and removing those succeeded. The final Apple
Groceries read returned no items. No account credentials or list contents are
recorded here.

After a successful complete read of both lists, the engine now retires obsolete
mappings if both lists are empty. It issues no remote writes in this case.
Nonempty-list uncertain-add protection and failed-read behavior are preserved.
This shared engine also serves Google Keep.

Validation: `python3 -m unittest discover -s tests -p test_keep_engine.py -v`
passed all 18 focused engine tests, including recovery after clearing both lists,
subsequent propagation of a new item, failed-read preservation, and existing
uncertain-add/duplicate protections. `git diff --check` passed. No full suite or
app build ran.

Owner-approved main integration and installation completed on 2026-11-08.
Marketplace confirmed installed commit `00c6f25` after Home Assistant restarted.
A fresh `coffey_table_recipes.sync_icloud` call at 13:53 EST returned `synced`,
zero changes and no error; the sensor confirmed zero mapped items and the Apple
Groceries entity remained at zero items. This verifies empty-list recovery live;
new-item propagation was exercised in focused tests, not against personal lists.

Installation initially failed because Marketplace warning acceptance is per HA
user. The connection uses a separate HA-MCP Server account. After explicit owner
approval, the supported `marketplace/warning/accept` API recorded its one-time
acceptance. `marketplace/info` confirmed `warning_accepted: true` before and after
restart. This removes that recurring installation blocker for this account.
