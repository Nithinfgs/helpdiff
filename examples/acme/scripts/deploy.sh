#!/usr/bin/env bash
# Nightly deploy used by the platform team.
set -euo pipefail

acme --config prod.toml deploy web --env staging --force --timeout 60
acme deploy api -e prod -f -v
acme deploy worker --format json | jq .
acme logs web -n 200 --follow
acme status --json > status.json
acme st
acme config get region
