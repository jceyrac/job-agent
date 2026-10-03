#!/usr/bin/env python3
"""Diff two parity fingerprints section by section (spec 031, FR-016).

Reads two fingerprint JSON files, walks them structurally, and prints a readable
report naming every differing path. Exits non-zero when any difference exists.
"""
from __future__ import annotations

import argparse
import json
import sys


def _diff(a, b, path: str = "") -> list[str]:
    diffs: list[str] = []
    if type(a) is not type(b):
        diffs.append(f"{path or '.'}: type {type(a).__name__} != {type(b).__name__}")
        return diffs
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a:
                diffs.append(f"{path}/{key}: missing in first")
            elif key not in b:
                diffs.append(f"{path}/{key}: missing in second")
            else:
                diffs.extend(_diff(a[key], b[key], f"{path}/{key}"))
    elif isinstance(a, list):
        if len(a) != len(b):
            diffs.append(f"{path}: length {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diffs.extend(_diff(x, y, f"{path}[{i}]"))
    else:
        if a != b:
            diffs.append(f"{path}: {a!r} != {b!r}")
    return diffs


def diff_fingerprints(a: dict, b: dict) -> list[str]:
    """Return human-readable difference paths; empty when identical."""
    return _diff(a, b)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diff two parity fingerprints.")
    parser.add_argument("a", help="first fingerprint JSON")
    parser.add_argument("b", help="second fingerprint JSON")
    args = parser.parse_args(argv)

    with open(args.a) as fh:
        a = json.load(fh)
    with open(args.b) as fh:
        b = json.load(fh)

    diffs = diff_fingerprints(a, b)
    if diffs:
        for line in diffs:
            print(line)
        print(f"{len(diffs)} difference(s) found")
        return 1
    print("fingerprints identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
