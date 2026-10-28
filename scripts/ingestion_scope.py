"""Platform ingestion scope — view/edit `config.ingestion_scope` (spec 040, FR-004).

The prose path for the platform scope (Constitution II): edits read-modify-write
the whole scope through `JobStorage.set_ingestion_scope` — no deploy, no LLM, no
scraping. An empty `--set-titles` means "open ingestion" (Constitution IX).

Usage:
  python -m scripts.ingestion_scope --show
  python -m scripts.ingestion_scope --set-titles "product manager,head of product"
  python -m scripts.ingestion_scope --set-exclude "junior,intern"
  python -m scripts.ingestion_scope --remote-or-hybrid on|off
  python -m scripts.ingestion_scope --set-locations "Switzerland,France"
  python -m scripts.ingestion_scope --set-boards "board1,board2"
"""
from __future__ import annotations

import argparse
import json
import sys

from core.paths import DB_PATH
from core.storage import JobStorage


def _split_terms(raw: str | None) -> list[str]:
    """Split comma/newline-separated input into trimmed, deduped terms."""
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace("\n", ",").split(",")]
    return list(dict.fromkeys(p for p in parts if p))


def _show(db: JobStorage) -> None:
    scope = db.get_ingestion_scope()
    titles = ", ".join(scope["title_families"]) or "(open)"
    exclude = ", ".join(scope["exclude"]) or "(none)"
    locations = ", ".join(scope["search_locations"]) or "(derived)"
    boards = ", ".join(scope["greenhouse_boards"]) or "(none)"
    print(json.dumps(scope, ensure_ascii=False, indent=2))
    print(f"title_families:    {titles}")
    print(f"exclude:           {exclude}")
    print(f"remote_or_hybrid:  {scope['remote_or_hybrid']}")
    print(f"search_locations:  {locations}")
    print(f"greenhouse_boards: {boards}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="ingestion_scope",
        description="View/edit the platform ingestion scope (config.ingestion_scope).",
    )
    parser.add_argument("--show", action="store_true",
                        help="print the current scope (default when no mutation flag is given)")
    parser.add_argument("--set-titles", metavar="LIST",
                        help="replace title_families (comma/newline-separated; empty = open)")
    parser.add_argument("--set-exclude", metavar="LIST",
                        help="replace exclude words (comma/newline-separated)")
    parser.add_argument("--remote-or-hybrid", choices=["on", "off"],
                        help="set remote_or_hybrid")
    parser.add_argument("--set-locations", metavar="LIST",
                        help="replace search_locations (comma/newline-separated)")
    parser.add_argument("--set-boards", metavar="LIST",
                        help="replace greenhouse_boards (comma/newline-separated)")
    args = parser.parse_args(argv)

    db = JobStorage(DB_PATH)
    scope = db.get_ingestion_scope()

    if args.set_titles is not None:
        titles = _split_terms(args.set_titles)
        if not titles:
            print("⚠️  empty title_families — ingestion is now OPEN", file=sys.stderr)
        db.set_ingestion_scope(titles, scope["exclude"], scope["remote_or_hybrid"])
    elif args.set_exclude is not None:
        exclude = _split_terms(args.set_exclude)
        db.set_ingestion_scope(scope["title_families"], exclude, scope["remote_or_hybrid"])
    elif args.remote_or_hybrid is not None:
        db.set_ingestion_scope(
            scope["title_families"], scope["exclude"], args.remote_or_hybrid == "on")
    elif args.set_locations is not None:
        db.set_ingestion_scope(
            scope["title_families"], scope["exclude"], scope["remote_or_hybrid"],
            search_locations=_split_terms(args.set_locations))
    elif args.set_boards is not None:
        db.set_ingestion_scope(
            scope["title_families"], scope["exclude"], scope["remote_or_hybrid"],
            greenhouse_boards=_split_terms(args.set_boards))

    _show(db)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
