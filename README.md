# CoffeyTableRecipes for Home Assistant

Connect your household's shared shopping list to Home Assistant. Add groceries,
check them off, restore checked items, rename items, or remove them using the
standard **To-do lists** screen, dashboard To-do list card, and `todo` actions.
All items, including completed items, are available through `todo.get_items`.
The list preserves each item's department and the app's department memory.

## Install

Requires Home Assistant 2025.12 or later and internet access to coffeybean.com
and Google's Firebase Authentication service. This is a custom integration,
not an official Home Assistant Core integration.

Add [this repository](https://github.com/agawlbb/home-assistant-coffey-table-recipes)
to **HACS → Custom repositories**, choose **Integration**, and download CoffeyTableRecipes.
Alternatively copy `custom_components/coffey_table_recipes` into your Home
Assistant `config/custom_components` folder. Restart Home Assistant.

Open **Settings → Devices & services → Add integration → CoffeyTableRecipes**.
Sign in with the email and password of a household owner or administrator.
If several households are available, select one; otherwise setup selects it
automatically. No YAML, API key, server address, or user ID is needed.

Google popup sign-in is not implemented in this initial version. Google-only
accounts need an email/password method linked to the **same Firebase account**;
do not create a second account or household. Use the app's password-reset flow
for your existing email, then sign in using that email and password.

## Household connection

Sign-in authorizes a separate, persistent **household integration token**.
The password and Firebase ID/refresh tokens are never stored. Home Assistant
stores only the household ID and its integration token in its normal config
entry storage; protect your HA configuration and backups accordingly.

The connection belongs to the household, independently of the person who set
it up. Other household members see the same list. Removing that person's
membership does not revoke the household connection. Only one HA config entry
per household is allowed on each installation. Multiple HA installations may
connect independently. Future household integration APIs can use this same
token without limiting it to shopping-list access; this release exposes only
the shopping list. Account/admin APIs never accept household tokens.

Removing the integration attempts to revoke its token on the server. An offline
removal cannot guarantee server revocation. A household administrator can list
and revoke household connections through the backend API (see backend docs).
Revoked or deleted-household tokens prompt reauthentication. Reconnection
must use an administrator of the same household, preserving entity identities.

## Shopping list and events

Your household appears as a device with a **Shopping list** To-do entity and
a **Shopping list changes** event entity. Add a To-do list dashboard card and
select the shopping entity. Completed items remain until explicitly removed.
Departments appear as read-only item descriptions and list attributes.
New items use the household's remembered department when available.

Use the native `todo.add_item`, `todo.update_item`, `todo.remove_item`,
`todo.remove_completed_items`, and `todo.get_items` actions. Use item UIDs when
automating changes to avoid ambiguity for duplicate names. Reordering and
department editing remain in CoffeyTableRecipes for this release.

For automations, choose the **Shopping list changes** event entity and its
`changed` event. The integration also emits the bus event
`coffey_table_recipes_shopping_list_changed`. Payloads contain `household_id`,
`config_entry_id`, `revision`, `added`, `removed`, `updated`, `items`, and
`departments`. Item records contain `id`, `name`, `checked`, and `department`.
Filter by household or config entry when several households are connected.
Payloads contain household shopping data; avoid publishing them in public logs.

Changes in HA update immediately. App changes are detected every 15 seconds by
default. Change this in **Settings → Devices & services → CoffeyTableRecipes →
Configure** (5–300 seconds). Initial loading and unchanged polls emit no event.
Changes between polls are coalesced; this is snapshot synchronization, not a
durable event log, and transient changes between polls may not be observed.
Revision-only or department/order changes also emit a change event.

### External list synchronization requests

Opening Shopping list in the app requests synchronization with an external list,
even if the shopping items have not changed. Use the **Shopping list
synchronization** event entity's `requested` event, or the bus event
`coffey_table_recipes_shopping_list_sync_requested`, to trigger your external-list
automation, or enable the built-in Google Keep synchronization below. Payloads contain `household_id`, `config_entry_id`, `request_id`,
`requested_at`, and `reason` (`shopping_list_opened`). The automation performs the
actual synchronization when no built-in Keep list is selected; receiving the event does not mean the lists are in sync.

Requests arrive on the next integration poll (15 seconds by default). Background
app refreshes and ordinary item edits do not request synchronization. The app
must be online and running a version containing this feature. Request failures
do not prevent viewing the shopping list and are not replayed automatically.

The server retains the latest 128 requests per household. HA remembers its last
request ID across reloads/restarts and emits each retained unseen request once
during normal operation. The first connection establishes a baseline without
replaying old requests. This is a bounded journal, not guaranteed delivery;
requests beyond retention or interruptions during event processing may be lost.

Concurrent edits reload the newest list and retry rejected revision conflicts
up to three times. Network failures are not automatically replayed, preventing
duplicate additions after an uncertain response. Refresh before retrying a
failed add. Offline lists become unavailable; edits require a connection.

## Optional Google Keep synchronization

Install and sign into **Google Keep Sync** (`google_keep_sync`) first and select
the Keep lists it should expose. In **Settings → Devices & services →
CoffeyTableRecipes → Configure**, choose the optional **Google Keep Sync list**.
Clear this field to disable syncing. CTR uses that integration's existing Google
authentication; no Google credentials are copied or requested by CTR. One Keep
list can belong to only one CTR household. Stored registry identities survive
entity renames and prevent a different list being substituted silently.

CTR and the selected Keep list synchronize additions, renames, checked/unchecked
status and deletions in both directions. Duplicate items stay distinct. CTR
departments and ordering remain under CTR's control. The first synchronization
merges both lists without treating unpaired items as deletions. Later deletions
propagate only when the counterpart has not been edited concurrently. Independent
field edits merge; CTR wins simultaneous conflicts on the same field. List order
and blank Keep separator rows are not synchronized.

Opening CTR's shopping list generates its existing household request. CTR handles
the resulting `coffey_table_recipes_shopping_list_sync_requested` event internally,
filtered by its `config_entry_id`, and immediately starts a fresh reconciliation.
CTR change events and HA `todo` edits targeting the selected Keep entity also
trigger it. Direct Keep-app changes are checked every **60 seconds**. CTR's own
polling interval is unchanged. In-flight explicit requests coalesce, and own CTR
writes do not create a sync loop.

Google Keep Sync 1.1.0's `request_sync` action has a 55-second cooldown and its
coordinator may return cached data after a failed refresh. CTR instead calls
`api.fetch_all_lists()`: errors propagate and successful reads provide fresh
evidence. Each write is read back before the persistent item ledger advances.
An offline, missing, deselected or trashed list stops syncing; it never means
"delete all items". An uncertain add is recovered from fresh reads and never
blindly repeated. Ambiguous/missing additions require manual reconciliation.

The **Google Keep synchronization** sensor on the CTR household device reports
`disabled`, `syncing`, `synced`, or `not_confirmed`, with `last_success`,
`last_reason`, `last_error`, `changes`, `mapped_items`.
`synced` requires fresh reads and complete verification of both lists. Last-success
time and item mapping survive restarts. Run `coffey_table_recipes.sync_google_keep`
for manual synchronization; specify `config_entry_id` when more than one household
is enabled. Clearing the selection stops future writes without undoing edits.

Remote services cannot provide an atomic transaction across both lists. A narrow
read/write race remains, phone uploads may be delayed by Google, and each run is
bounded to 100 changes and 180 seconds. No sync is reported as confirmed after
an authentication/network error. This adapter uses Google Keep Sync's internal
1.1.0 API; future upstream API changes may require compatibility updates.

## Apple iCloud shopping list synchronization

Version 0.4.0 adds an optional **Apple iCloud shopping list** selector in
**Settings → Devices & services → CoffeyTableRecipes → Configure**.
First configure Home Assistant's built-in [Apple iCloud integration](https://www.home-assistant.io/integrations/icloud/)
and make sure its Apple Reminders shopping list appears as an enabled `todo` entity.
This requires a Home Assistant version with iCloud Reminders support (2026.10 or
later). Select that list in CTR's settings; Apple authentication and approval of
access stay in the iCloud integration. CTR never stores an Apple password.

The first synchronization merges both lists. Subsequent additions, renames,
checked/unchecked states and deletions synchronize in both directions. Duplicate
items keep separate identities, and CTR departments are preserved. Existing Apple
notes and due dates are retained when updating a reminder; those fields and Apple's
grocery categories are not copied into CTR departments. The same three-way merge
and uncertain-add protections used for Keep apply, with a separate persistent
mapping for each household and selected Apple list. Switching lists starts a new
merge; clearing the selector stops synchronization without deleting items.

CTR changes and scoped app sync requests trigger reconciliation. Completed iCloud
coordinator refreshes, including Home Assistant edits, also trigger reconciliation.
A 60-second poll fetches fresh iCloud reminders for direct Apple Reminders changes.
Failed refreshes, a missing list, or unreadable encrypted reminders stop the run
without treating cached/partial results as deletions. Approve Apple access on an
Apple device if the status reports an encryption problem.

The **Apple iCloud synchronization** sensor reports `disabled`, `syncing`, `synced`
or `not_confirmed`, with the last successful sync, error and mapped item count.
The `coffey_table_recipes.sync_icloud` action requests a fresh sync; specify
`config_entry_id` when multiple CTR households have iCloud synchronization enabled.
Google Keep remains independently optional: select either provider or both. Both
selected lists share the household's CTR shopping items, so changes can propagate
to both external lists. Leave Keep unselected if moving exclusively to Apple.

## Development

Use Python 3.14 and install `requirements-test.txt`. The test fixture package pins
Home Assistant 2026.10.2; aiohttp is explicitly pinned to its matching 3.14.5
release. CI runs `python -m pip check` and `python -m pip_audit --progress-spinner off`
against the installed dependency set before the tests. These are development
dependencies; the installed integration uses Home Assistant's managed runtime.

Run `python -m unittest discover -s tests -p test_api.py -v` for the isolated client tests.
Full HA integration tests are configured in CI on Linux with the supported HA
test package. See `VALIDATION.md` for what has actually been verified.
No app builds or private Firebase configuration belong in this repository.

## License

MIT. See [LICENSE](LICENSE).

## Shopping WebSocket migration branch

`migration/shopping-websocket` (base `aa5fc4f`) moves shopping traffic to
`wss://ctr.coffeybean.com/shopping`. Account sign-in and integration credential
management remain on inMotion. Backend deployment includes credential preparation
and retires the old shopping HTTP routes; no separate switch is required.

The integration maintains an authenticated household connection, immediately
publishes pushed changes, and reconnects automatically. Its existing refresh
interval also reconciles through that socket. The confirmed snapshot and pending
receipt-backed changes persist in HA storage. Edits return after durable local
storage; acknowledgements clear the outbox. Keep/iCloud verification reads still
require successful remote readback. Pending edits survive HA restarts and lost
responses; unrelated pushed changes do not undo local pending deletions.

All three repositories remain on task branches until testing and user merge
approval. This source push does not install or update a running HA instance.
The backend repository's `docs/shopping-websocket-migration.md` contains the
complete backend deployment entry point; there is no separate cutover switch.
