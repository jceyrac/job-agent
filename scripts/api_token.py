"""API token management CLI (spec 041, FR-004 / FR-013).

The **only** creator/revoker of API tokens in v1 — the API itself never mints
or revokes tokens. Tokens carry a subset of `API_TOKEN_SCOPES` (`read:catalogue`,
`read:me`, `write:me`, `write:catalogue`, `tasks:run`, `tasks:pipeline`) and are
hashed at rest (only a SHA-256 digest is stored); the raw token is printed
exactly once by `create` and never persisted.

Usage:
  python -m scripts.api_token create --name <name> --scopes read:catalogue,read:me \
      [--user <id>] [--expires-in <days>]
  python -m scripts.api_token list [--user <id>] [--json]
  python -m scripts.api_token revoke <id>
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from core.paths import DB_PATH
from core.storage import API_TOKEN_SCOPES, JobStorage


def _split_scopes(raw: str) -> list[str]:
    """Split comma-separated scopes into a trimmed, deduped list."""
    return list(dict.fromkeys(p.strip() for p in raw.split(",") if p.strip()))


def _scopes_for_display(raw: str | None) -> str:
    """Decode a stored `scopes` JSON column to a comma list for display."""
    try:
        scopes = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        scopes = []
    return ", ".join(scopes) if scopes else "(none)"


def _now_plus(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _cmd_create(args: argparse.Namespace, db: JobStorage) -> int:
    scopes = _split_scopes(args.scopes)
    unknown = [s for s in scopes if s not in API_TOKEN_SCOPES]
    if unknown:
        print(f"error: unknown scope(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"valid scopes: {', '.join(sorted(API_TOKEN_SCOPES))}", file=sys.stderr)
        return 2

    expires_at = _now_plus(args.expires_in) if args.expires_in else None
    raw = db.create_api_token(args.name, scopes, user_id=args.user, expires_at=expires_at)
    row = db.resolve_api_token(raw)  # gives us the token id (hash lookup, no secret)
    print(raw)  # the raw token, exactly once
    print(f"created token {row['id']} for user {row['user_id']} "
          f"(scopes: {', '.join(row['scopes'])})")
    return 0


def _cmd_list(args: argparse.Namespace, db: JobStorage) -> int:
    rows = db.list_api_tokens(user_id=args.user)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("(no tokens)")
        return 0

    headers = ["id", "user_id", "name", "scopes", "created_at",
               "last_used_at", "revoked_at", "expires_at"]
    widths = {h: len(h) for h in headers}
    for r in rows:
        for h in headers:
            widths[h] = max(widths[h], len(str(r.get(h) or "")))
    print("  ".join(h.ljust(widths[h]) for h in headers))
    for r in rows:
        display = {h: (r.get(h) or "") for h in headers}
        display["scopes"] = _scopes_for_display(r.get("scopes"))
        print("  ".join(str(display[h]).ljust(widths[h]) for h in headers))
    return 0


def _cmd_revoke(args: argparse.Namespace, db: JobStorage) -> int:
    try:
        db.revoke_api_token(args.id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"revoked {args.id}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="api_token",
        description="Create/list/revoke scoped API tokens (spec 041/042).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="mint a new token (prints it once)")
    p_create.add_argument("--name", required=True, help="operator label")
    p_create.add_argument("--scopes", required=True,
                          help="comma-separated subset of read:catalogue,read:me,write:me,write:catalogue,tasks:run,tasks:pipeline")
    p_create.add_argument("--user", default=None, help="token owner (default: default user)")
    p_create.add_argument("--expires-in", type=int, default=None, metavar="DAYS",
                          help="expire after N days (default: no expiry)")

    p_list = sub.add_parser("list", help="list tokens (never shows the secret)")
    p_list.add_argument("--user", default=None, help="restrict to one user (default: all)")
    p_list.add_argument("--json", action="store_true", help="emit JSON instead of a table")

    p_revoke = sub.add_parser("revoke", help="revoke a token by id")
    p_revoke.add_argument("id", help="token id (from `list`)")

    args = parser.parse_args(argv)

    if args.command == "create" and args.expires_in is not None and args.expires_in <= 0:
        print("error: --expires-in must be a positive number of days", file=sys.stderr)
        return 2

    db = JobStorage(DB_PATH)

    if args.command == "create":
        return _cmd_create(args, db)
    if args.command == "list":
        return _cmd_list(args, db)
    return _cmd_revoke(args, db)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
