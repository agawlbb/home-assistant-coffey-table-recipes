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
`last_reason`, `last_error`, `changes`, `mapped_items` and migration information.
`synced` requires fresh reads and complete verification of both lists. Last-success
time and item mapping survive restarts. Run `coffey_table_recipes.sync_google_keep`
for manual synchronization; specify `config_entry_id` when more than one household
is enabled. Clearing the selection stops future writes without undoing edits.

### Replacing the standalone bridge

CTR detects a matching `ctr_keep_bridge` entry during setup, unloads/disables it
before starting a new writer, imports its final mapping (including pending adds)
through HA's `Store` API, and carries over its last-success time. Its previous
store is retained for recovery. After verifying the native status and list
contents, remove the old bridge entry and its `custom_components/ctr_keep_bridge`
files. Google Keep Sync stays installed and signed in. A still-running old bridge
blocks native writes. Changing Keep selections uses separate ledgers, so a prior
list's mapping cannot be applied to a new list.

Remote services cannot provide an atomic transaction across both lists. A narrow
read/write race remains, phone uploads may be delayed by Google, and each run is
bounded to 100 changes and 180 seconds. No sync is reported as confirmed after
an authentication/network error. This adapter uses Google Keep Sync's internal
1.1.0 API; future upstream API changes may require compatibility updates.

## Development

Run `python -m unittest discover -s tests -p test_api.py -v` for the isolated client tests.
Full HA integration tests are configured in CI on Linux with the supported HA
test package. See `VALIDATION.md` for what has actually been verified.
No app builds or private Firebase configuration belong in this repository.

## License

MIT. See [LICENSE](LICENSE).
