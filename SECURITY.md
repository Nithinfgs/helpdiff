# Security Policy

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** (Security tab → "Report a vulnerability"). Do not open a public issue for security problems. Expect an acknowledgement within a few days.

## Scope and threat model

- `helpdiff snap` / `verify` execute the command you point them at with `--help` / `--version`, and then run subcommand paths that command's own help text lists. This is by design; do not crawl untrusted binaries. A bug that makes helpdiff run anything *else* (for example, executing text found in a script or snapshot) is in scope.
- `diff`, `check`, `show`, `parse` only read files. Snapshots are plain JSON and are never executed.
- helpdiff makes no network connections.
