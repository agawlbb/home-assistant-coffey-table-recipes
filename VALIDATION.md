# Validation — October 18, 2026

Runtime source: `3b6010611afc25a27fadf78d2cee19ea4d228eed`.
All tests passed in [Linux CI run 39148209297](https://github.com/agawlbb/home-assistant-coffey-table-recipes/actions/runs/39148209297):

- 10 isolated client tests: add/check/uncheck/rename/remove, stable IDs across
  conflict retries, preservation of concurrent edits/departments, bounded
  conflicts, missing items, no replay after uncertain network failures, transient
  login credentials, malformed snapshots, and safe HTTP authorization errors.
- 7 tests using real Home Assistant 2025.12.0 and its test fixtures: sign-in and
  household-only stored credentials, invalid login/member-only denial, duplicate
  household prevention, complete and incomplete To-do items, event entity and bus
  delivery without duplicate unchanged events, standard To-do actions, auth-failure
  reauthentication, options/reload, original-household enforcement and unloading.

The 10 client tests also passed locally on Windows. Installing the full HA test
environment locally was blocked by a native C dependency requiring Visual C++;
the full integration suite ran successfully on Linux instead. The test
dependencies pin pycares below 5 to match the older HA test environment's aiodns.

## Hosted backend

Deployed server commit `7c2e3742b37d67611ed82bcc3386b8aeee6652cd` passed
[backend deployment run 39147995236](https://github.com/agawlbb/coffey-table-recipes-server/actions/runs/39147995236).
Local PHP connection fixtures and shopping-list fixtures passed. Four new
deployment configuration tests passed, and the backend workflow passed its full
PHP/Python checks. No new database schema was required.

Independent HTTPS checks verified the matching backend receipt, HTTP 200 health,
HTTP 401 on unauthenticated list reads, revocation and connection management,
and HTTP 403 for private configuration and connection storage. The public
Firebase client identifier is available through the setup endpoint. An
intentionally nonexistent fixture login produced Firebase's expected invalid
credentials response, verifying the configured sign-in boundary without using a
real account or creating users.

## Limits of verification

No real household shopping items were modified and no live HA installation was
changed. Successful real-account sign-in and interactive appearance on the user's
HA installation still need an installation check. Config flow and entity behavior
are verified through Home Assistant's real test framework with mocked remote
responses. Polling provides snapshot change notifications, not guaranteed delivery
of every intermediate app edit. Google popup sign-in is not implemented; use a
password method linked to the existing account. No Flutter builds were produced.
