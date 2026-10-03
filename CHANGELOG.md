# Changelog

## 0.1.0 - 2026-10-03

First release.

- `snap` / `verify`: crawl a CLI's `--help` tree (argparse, optparse, click, cobra, clap, commander layouts) into a deterministic JSON snapshot.
- `diff`: classify removed/renamed/changed commands, flags, aliases, choices, defaults and arguments as breaking, warning or info, with rename and moved-command hints. Text, Markdown and JSON output.
- `--scripts` / `check`: find the shell, Makefile, CI YAML, Dockerfile and Markdown lines that an upgrade breaks, reporting only regressions relative to the old snapshot.
