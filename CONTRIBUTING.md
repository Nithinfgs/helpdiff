# Contributing

Thanks for helping. The most useful contributions are **help outputs that parse wrongly**.

## Add a parser fixture

1. Save the tool's help text to `tests/fixtures/<tool>.txt` (trim it to a representative size; keep it free of anything sensitive).
2. Check how helpdiff reads it: `helpdiff parse tests/fixtures/<tool>.txt --known <program-name>`
3. Add a test in `tests/test_parser.py` asserting the flags, subcommands and positionals you expect.
4. Fix `src/helpdiff/parser.py` until it passes without breaking the other fixtures.

## Development

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
mypy
```

- Python 3.9+; **no runtime dependencies**. Please keep it that way.
- Keep the parser conservative: ignoring text is better than inventing flags.
- Regenerate the demo and example snapshots with `python3 scripts/make_demo.py` if output formatting changes.
- Commit style: `feat:`, `fix:`, `docs:`, `test:`, `chore:`, `ci:`.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
