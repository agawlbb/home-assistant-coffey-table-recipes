# Repository workflow

## GitHub, branches and workspaces

- GitHub `agawlbb/home-assistant-coffey-table-recipes`, default branch `main`, is the canonical copy of accepted code and history. Laptop, VPS and cloud checkouts are working copies. The default branch is for integration of completed work, never a working branch for a task.
- Prefer the VPS for most development. Development may also take place on the laptop or another authorized environment, including emulator, webview and device testing. Independent tasks use separate, clearly named temporary branches and separate working directories (Git worktrees or clones). Do not share a writable checkout between concurrent tasks. There is no permanent designated chat owner or owner-chat approval requirement.
- Before editing, read applicable instructions and inspect current branches, worktrees, local edits, live GitHub heads and active writers/builds. Preserve unrelated and unpushed work; a clean tree alone does not prove inactivity. Coordinate overlapping changes and shared resources with the tasks involved.
- Before starting new work, fetch from GitHub (`git fetch origin`) and create the temporary task branch from the latest fetched remote default (`origin/<default>`, where `<default>` is this repository's `main`, `master` or `work` branch identified above). Record the task's purpose and base commit; do not branch from a stale local default.
- Before resuming existing work, fetch from GitHub and inspect the intended task branch, its upstream and any divergence from the remote task branch and canonical default. Reconcile updates intentionally without overwriting uncommitted, unpushed or unrelated changes. Preserve local work before switching branches or integrating updates, and resolve conflicts without discarding another task's work. Do not blindly run `git pull` in the current branch or pull another task's branch into it.
- To move the same task between machines, commit and push that task branch, then fetch and continue the intended branch on the other machine after coordinating the handoff and following the resumption checks above. Never overwrite another task's work or use recovery archives as active workspaces.
- Before merging, fetch and integrate the latest canonical default into the task branch, resolve conflicts without discarding others' changes, review the diff and rerun relevant checks against the integrated result. Serialize merges into the default branch and recheck its live head before publishing. Respect existing protections; do not bypass them or rewrite published history.
- Commit and push completed work to the existing GitHub origin, merge accepted work into `main`, and verify the remote commit and a clean task working tree before reporting completion. Report blockers or unsynchronized work explicitly. Inspect push triggers before every push; GitHub publication does not itself authorize builds, releases, deployment or installation.
- Temporary task branches must be removed both locally and remotely once their purpose is fulfilled and their changes are merged into the canonical default branch, or their work is explicitly abandoned by the user or the person responsible for that task. Verify the merge or abandonment decision first; preserve commit history, required recovery evidence and recoverable unique work before deletion. Stale or paused work is not abandoned. Never discard unmerged work without that decision, delete another task's branches, or purge recovery archives as routine cleanup.

## Project boundaries

- This standalone Home Assistant integration, the Flutter client and PHP server remain separate repositories. Existing laptop integration checkout: `C:\Users\mailb\StudioProjects\home-assistant-coffey-table-recipes`.
- Keep credentials, tokens, private Firebase settings, Home Assistant account backups and personal data out of Git. Do not create another remote repository or rewrite published history without explicit authorization.
- Follow the user's requested scope. Instruction changes do not authorize functionality/dependency changes, builds, installation, deployment, releases, or repository/security settings changes. A source push does not authorize updating a running Home Assistant installation.

## Keep the project simple

Avoid overengineering. Treat these as small personal projects unless the user says
otherwise. Use the simplest maintainable implementation that meets the current
requirements. Brief deployment downtime (including a couple of minutes) is
acceptable. Prefer copying files and restarting a process over release frameworks,
zero-downtime machinery, repeated setup migrations, compatibility modes, feature
flags or abstractions for hypothetical future scale. Keep setup separate from
routine deployment. Preserve authentication, credential privacy and protection
against real data loss, and use proportionate tests. Explain a concrete need before
adding complexity; do not introduce unrequested features or operational workflows.
