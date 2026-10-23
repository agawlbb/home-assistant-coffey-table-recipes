# Repository ownership and workflow

- Joe assigned this repository to the existing **CTR owner** chat
  (`01a15fd0-827e-77c3-a0a3-737af531ea3f`) on October 23, 2026, alongside
  the Coffey Table Recipes Flutter client and PHP server. This is chat/project
  coordination ownership; it does not change GitHub ownership or permissions.
- Canonical checkout: `C:\Users\mailb\StudioProjects\home-assistant-coffey-table-recipes`.
  Existing origin: `https://github.com/agawlbb/home-assistant-coffey-table-recipes.git`.
  This is the standalone Home Assistant integration repository; the client and
  server remain separate repositories with their own workflow instructions.
- Other chats must coordinate with CTR owner and obtain a clean checkout
  handoff before writing or switching branches. Check current branches,
  worktrees, local edits, GitHub heads and active writers/tests/builds. Use this
  canonical checkout; preserve unrelated work and recovery archives.
- Complete and appropriately verify authorized work, commit it and push to the
  existing origin. Inspect workflow push triggers before each push. Before
  reporting completion, verify a clean working tree and matching local/GitHub
  branch heads. Respect explicit instructions to keep work local.
- Keep credentials, tokens, private Firebase settings, Home Assistant account
  backups and personal data out of Git. Do not create a competing checkout or
  remote repository, overwrite unrelated changes, or rewrite published history
  without explicit authorization.
- Ownership assignment alone does not authorize functionality/dependency
  changes, builds, installation, deployment, releases, or repository/security
  settings changes. A source push does not authorize updating a running Home
  Assistant installation. Follow the user's requested scope.
