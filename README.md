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

After this repository is published, add its GitHub URL to **HACS → Custom
repositories**, choose **Integration**, and download CoffeyTableRecipes.
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

Concurrent edits reload the newest list and retry rejected revision conflicts
up to three times. Network failures are not automatically replayed, preventing
duplicate additions after an uncertain response. Refresh before retrying a
failed add. Offline lists become unavailable; edits require a connection.

## Development

Run `python -m unittest discover -s tests -v` for the isolated client tests.
Full HA integration tests are configured in CI on Linux with the supported HA
test package. See `VALIDATION.md` for what has actually been verified.
No app builds or private Firebase configuration belong in this repository.

## License

MIT. See [LICENSE](LICENSE).
