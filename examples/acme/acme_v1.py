#!/usr/bin/env python3
"""acme 1.x: a small deployment CLI used to demo helpdiff (does nothing real)."""

import argparse


def build():
    p = argparse.ArgumentParser(prog="acme", description="Deploy and inspect services.")
    p.add_argument("--version", action="version", version="acme 1.9.0")
    p.add_argument("--config", metavar="FILE", help="config file (default: acme.toml)")
    sub = p.add_subparsers(dest="cmd", title="commands", metavar="<command>")

    d = sub.add_parser("deploy", help="deploy a service")
    d.add_argument("target", help="service to deploy")
    d.add_argument("-e", "--env", choices=["dev", "staging", "prod"], default="dev", help="target environment")
    d.add_argument("-f", "--force", action="store_true", help="skip the confirmation prompt")
    d.add_argument("--timeout", type=int, default=30, help="seconds to wait (default: 30)")
    d.add_argument("-v", "--verbose", action="store_true", help="print progress")
    d.add_argument("--format", choices=["text", "json"], default="text", help="output format")

    lg = sub.add_parser("logs", help="stream service logs")
    lg.add_argument("service", help="service name")
    lg.add_argument("-n", "--lines", type=int, default=100, help="lines to show (default: 100)")
    lg.add_argument("--follow", action="store_true", help="keep streaming")

    st = sub.add_parser("status", help="show service status", aliases=["st"])
    st.add_argument("--json", action="store_true", help="machine-readable output")

    cfg = sub.add_parser("config", help="inspect configuration")
    cs = cfg.add_subparsers(dest="sub", title="commands", metavar="<command>")
    cs.add_parser("get", help="print a value").add_argument("key", help="config key")
    cs.add_parser("list", help="print all values")
    return p


if __name__ == "__main__":
    build().parse_args()
