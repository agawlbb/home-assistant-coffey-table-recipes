# Validation — October 18, 2026

## Current CI dependencies — October 20, 2026

Commit `ad41764` passed all 57 tests in
[Linux CI run 39411036851](https://github.com/agawlbb/home-assistant-coffey-table-recipes/actions/runs/39411036851)
using Python 3.14, pytest-homeassistant-custom-component 0.13.373,
Home Assistant 2026.10.2 and aiohttp 3.14.5. The obsolete pycares restriction and
older Home Assistant fixture pin were removed. The current resolver installed
aiodns 4.0.4 and pycares 5.2.1 without the former compatibility workaround.

`python -m pip check` passed. pip-audit 2.10.1 audited the installed environment
and reported no known vulnerabilities; no advisories were suppressed. CI now
checks dependency consistency and runs that audit before every test run.
Tests are invoked with `python -m pytest` so project imports remain available
under the updated pytest stack. These changes affect CI/development dependencies
only; no installed Home Assistant packages, integration runtime code, or app
builds were changed. Audit results reflect the advisory data available at the
time of that run, rather than a guarantee against future vulnerabilities.

## Reduced service surface — October 20, 2026 (0.4.1)

Runtime commit `2de3550` passed all 57 tests in
[Linux CI run 39407925976](https://github.com/agawlbb/home-assistant-coffey-table-recipes/actions/runs/39407925976).
The unused configuration-deletion and component-archiving functionality was
removed entirely, together with its setup compatibility path, metadata,
documentation and obsolete tests. A real HA regression test verifies that the
domain registers exactly `sync_google_keep` and `sync_icloud`.

The targeted one-time metadata cleanup separately passed
[run 39407598047](https://github.com/agawlbb/home-assistant-coffey-table-recipes/actions/runs/39407598047),
including preservation of unrelated connection data and current item mappings.
That temporary cleanup was applied on the installed instance, verified through
read-only storage inspection, then removed from the final source.

Obsolete installed source archives, orphaned item storage, local archived source,
deployment packages and outdated deployment notes were deleted. Current working
files were searched for stale references. Existing Git history and full-system
backup snapshots were not rewritten or deleted.

Installed 0.4.1 through HACS and verified after restart: both Keep and iCloud
reported `synced` with no errors or changes; all three lists matched their
pre-update snapshots. The live domain exposed exactly the two native sync
actions. Read-only inspection confirmed the removed functionality and obsolete
metadata were absent from installed source, and the obsolete component,
dedicated recovery directory and orphaned store were absent from disk.

## Optional Apple iCloud list synchronization — October 19, 2026 (0.4.0)

Runtime commit `21d3a2e` passed all 60 tests in
[Linux CI run 39244468045](https://github.com/agawlbb/home-assistant-coffey-table-recipes/actions/runs/39244468045).
The 37 isolated API/engine/Keep-adapter tests also passed locally on Windows.
Seven new real-HA tests cover iCloud mapping and status persistence, failed and
partially decrypted refreshes without writes, preservation of Apple notes/due
dates in service calls, scoped app requests, CTR/HA edit triggers, 60-second
polling, unloading, optional selection/clearing, list ownership, and Apple-to-CTR
renaming with CTR departments retained. Remote Apple/CTR responses are mocked in
these tests; the existing reconciliation engine's bidirectional and duplicate
checks are reused.

The adapter follows Home Assistant 2026.10.2's iCloud reminder coordinator and
native To-do actions, using the existing iCloud account. Refresh failures and
skipped unreadable reminders block reconciliation. No Apple credentials, private
list identities, or household contents are included in this repository.

Installed through HACS and restarted Home Assistant successfully. The live CTR
entry loaded, its options schema exposes an optional selector restricted to
`icloud` To-do entities, and the new Apple synchronization sensor reports
`disabled` until a list is selected. Existing Keep synchronization resumed as
`synced` with its 15 mappings and no changes. No Apple list was selected or
modified during this installation check; real Apple end-to-end reconciliation
remains unverified. The optional selector lets the household choose its list.

## Native Google Keep synchronization (0.3.0)

The API, reconciliation engine, adapter and HA lifecycle tests passed in
[Linux CI run 39156956847](https://github.com/agawlbb/home-assistant-coffey-table-recipes/actions/runs/39156956847).
Live temporary-item checks verified additions and deletions in both directions,
Keep rename/check/uncheck delivery to CTR, CTR check/uncheck delivery to Keep,
scoped synchronization requests and 60-second polling. Original item identities
and CTR departments were preserved after test items were removed.

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

## External list synchronization requests (0.2.0)

Runtime commit `961b791` passed [Linux run 39148980874](https://github.com/agawlbb/home-assistant-coffey-table-recipes/actions/runs/39148980874):
10 client tests and 9 real HA lifecycle tests. The new checks deliver multiple
requests without item changes, verify the separate event entity and bus payloads,
persist the request cursor without reloading on each event, suppress repeats
after polling/reload, and baseline existing history on initial connection.

The Flutter source passed 11 focused widget checks and source analysis, including
once-per-opening requests and nonblocking failures. App commit `51221f5` is source
only; no app build or hosted Flutter deployment was requested or produced.
The PHP request-journal fixtures passed persistence, household isolation, ordered
IDs, non-mutating reads and the 128-request retention bound. Delivery of a real
app-open event through an installed HA instance remains unverified.

Backend synchronization support was deployed successfully as
`8ec35404908a745cc549735580e8c3164d9c124b` in
[run 39148973036](https://github.com/agawlbb/coffey-table-recipes-server/actions/runs/39148973036).
Independent HTTPS checks confirmed the matching receipt, HTTP 200 health,
HTTP 401 for unauthenticated request creation and integration reads, HTTP 405
for GET on the POST-only request endpoint, and HTTP 403 on journal storage.
No real household data or sync requests were changed during hosted verification.

## Patched CI harness - October 21, 2026

Home Assistant 2026.10.2 pins vulnerable uv 0.12.23 and Pillow 12.3.0.
The CI-only overrides install uv 0.13.4 and Pillow 13.0.0; they do not modify
an installed Home Assistant system. The dependency checker permits precisely
these two upstream metadata discrepancies and rejects any others. Advisory
checking remains enabled without suppressions, followed by the complete suite.

On JoeLaptop, Python 3.14.8 installed this environment successfully. The
version/consistency checks and PNG/TIFF/JPEG round trips passed, as did all 35
isolated API, Keep engine and adapter tests. Full HA pytest startup requires
POSIX fcntl, unavailable on Windows; Linux CI supplies that runtime validation.
