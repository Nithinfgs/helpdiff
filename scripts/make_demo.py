#!/usr/bin/env python3
"""Regenerate docs/assets/demo.svg and the example snapshots from the real tool output.

Run from the repository root:  python3 scripts/make_demo.py
Nothing in the SVG is typed by hand: every output line comes from running helpdiff.
"""

import html
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACME = os.path.join(ROOT, "examples", "acme")
ENV = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"), NO_COLOR="")
ENV.pop("NO_COLOR")

COLORS = {"31": "#ff7b72", "32": "#7ee787", "33": "#e3b341", "36": "#79c0ff"}
FG, DIM = "#e6edf3", "#8b949e"
LINE_H, CHAR_W, PAD = 19, 8.3, 22


def run(args):
    proc = subprocess.run([sys.executable, "-m", "helpdiff"] + args, cwd=ACME, env=ENV, capture_output=True, text=True)
    return proc.stdout, proc.stderr


def spans(line):
    """Split an ANSI-coloured line into (text, color, bold, dim) runs."""
    out, color, bold, dim, pos = [], FG, False, False, 0
    for m in re.finditer(r"\x1b\[([0-9;]*)m", line):
        if m.start() > pos:
            out.append((line[pos : m.start()], color, bold, dim))
        for code in m.group(1).split(";"):
            if code in ("", "0"):
                color, bold, dim = FG, False, False
            elif code == "1":
                bold = True
            elif code == "2":
                dim = True
            elif code in COLORS:
                color = COLORS[code]
        pos = m.end()
    if pos < len(line):
        out.append((line[pos:], color, bold, dim))
    return out


def main():
    snaps = os.path.join(ACME, "snapshots")
    os.makedirs(snaps, exist_ok=True)
    cmds = [
        (
            'helpdiff snap "python3 acme_v1.py" --name acme -o snapshots/acme-1.9.0.json',
            ["snap", "python3 acme_v1.py", "--name", "acme", "-o", "snapshots/acme-1.9.0.json"],
        ),
        (
            'helpdiff snap "python3 acme_v2.py" --name acme -o snapshots/acme-2.0.0.json',
            ["snap", "python3 acme_v2.py", "--name", "acme", "-o", "snapshots/acme-2.0.0.json"],
        ),
        (
            "helpdiff diff snapshots/acme-1.9.0.json snapshots/acme-2.0.0.json --scripts scripts/",
            [
                "diff",
                "snapshots/acme-1.9.0.json",
                "snapshots/acme-2.0.0.json",
                "--scripts",
                "scripts/",
                "--color",
                "always",
            ],
        ),
    ]
    rows = []  # (kind, ansi_text)
    for shown, args in cmds:
        out, err = run(args)
        if args[0] == "snap":  # regenerate the example snapshots; the picture starts at the diff
            continue
        rows.append(("prompt", shown))
        for line in (err + out).splitlines():
            rows.append(("out", line))
        rows.append(("out", ""))
    while rows and rows[-1][1] == "":
        rows.pop()

    width = (
        int(max(len(re.sub(r"\x1b\[[0-9;]*m", "", t)) + (2 if k == "prompt" else 0) for k, t in rows) * CHAR_W)
        + 2 * PAD
    )
    width = max(width, 820)
    height = 44 + len(rows) * LINE_H + PAD
    total = 4.0 + len(rows) * 0.11 + 5.0
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img" '
        'aria-label="helpdiff terminal demo: diffing two versions of a CLI and listing the scripts that break">'
        % (width, height, width, height),
        "<style>",
        "text{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,'DejaVu Sans Mono',monospace;font-size:13px;white-space:pre}",
        ".l{animation-duration:%.2fs;animation-timing-function:steps(1,end);animation-iteration-count:infinite}"
        % total,
        "</style>",
        '<rect width="%d" height="%d" rx="10" fill="#0d1117"/>' % (width, height),
        '<rect width="%d" height="30" rx="10" fill="#161b22"/><rect y="20" width="%d" height="10" fill="#161b22"/>'
        % (width, width),
        '<circle cx="18" cy="15" r="5" fill="#ff5f56"/><circle cx="36" cy="15" r="5" fill="#ffbd2e"/><circle cx="54" cy="15" r="5" fill="#27c93f"/>',
    ]
    t = 0.6
    for i, (kind, text) in enumerate(rows):
        y = 52 + i * LINE_H
        pct = t / total * 100
        runs = []
        if kind == "prompt":
            runs.append('<tspan fill="#7ee787">$ </tspan><tspan fill="%s">%s</tspan>' % (FG, html.escape(text)))
            t += 0.9
        else:
            for chunk, color, bold, dim in spans(text):
                runs.append(
                    '<tspan fill="%s"%s%s>%s</tspan>'
                    % (
                        DIM if dim else color,
                        ' font-weight="bold"' if bold else "",
                        ' opacity="0.85"' if dim else "",
                        html.escape(chunk),
                    )
                )
            t += 0.11
        parts.append(
            '<g class="l" style="animation-name:s%d"><text x="%d" y="%d">%s</text></g>' % (i, PAD, y, "".join(runs))
        )
        parts.insert(5, "@keyframes s%d{0%%{opacity:0}%.2f%%{opacity:1}100%%{opacity:1}}" % (i, pct))
    parts.insert(5, "")
    parts.append("</svg>")
    path = os.path.join(ROOT, "docs", "assets", "demo.svg")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    svg = "\n".join(parts)
    # keyframes were inserted inside <style>: order is irrelevant for CSS
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg + "\n")
    print("wrote", os.path.relpath(path, ROOT), "(%d bytes)" % len(svg))


if __name__ == "__main__":
    main()
