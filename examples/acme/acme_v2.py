#!/usr/bin/env python3
"""acme 2.x: the next release of the demo CLI. A few things moved."""

import argparse


def build():
    p = argparse.ArgumentParser(prog="acme", description="Deploy and inspect services.")
    p.add_argument("--version", action="version", version="acme 2.0.0")
    p.add_argument("--config", metavar="FILE", help="config file (default: acme.toml)")
    sub = p.add_subparsers(dest="cmd", title="commands", metavar="<command>")

    d = sub.add_parser("deploy", help="deploy a service")
    d.add_argument("target", help="service to deploy")
    d.add_argument("region", help="region to deploy into")
    d.add_argument("-e", "--env", choices=["dev", "prod"], default="dev", help="target environment")
    d.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    d.add_argument("--timeout", type=int, default=10, help="seconds to wait (default: 10)")
    d.add_argument("-v", "--verbose", metavar="LEVEL", help="print progress at this level")
    d.add_argument("--format", choices=["text", "json", "yaml"], default="text", help="output format")
    d.add_argument("--dry-run", action="store_true", help="show what would happen")

    svc = sub.add_parser("service", help="work with running services")
    ss = svc.add_subparsers(dest="sub", title="commands", metavar="<command>")
    lg = ss.add_parser("logs", help="stream service logs")
    lg.add_argument("service", help="service name")
    lg.add_argument("-n", "--lines", type=int, default=100, help="lines to show (default: 100)")
    lg.add_argument("-f", "--follow", action="store_true", help="keep streaming")

    st = sub.add_parser("status", help="show service status")
    st.add_argument("--output", choices=["text", "json"], default="text", help="output format")

    cfg = sub.add_parser("config", help="inspect configuration")
    cs = cfg.add_subparsers(dest="sub", title="commands", metavar="<command>")
    cs.add_parser("get", help="print a value").add_argument("key", help="config key")
    cs.add_parser("list", help="print all values")
    return p


if __name__ == "__main__":
    build().parse_args()
