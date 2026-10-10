"""cli.py — the human driver loop for the CV agent (spec 029).

Run::

    python -m cv_agent.cli <job_id>            # existing scored job
    python -m cv_agent.cli <url>               # live careers/ATS URL
    python -m cv_agent.cli --paste --letter    # pasted posting text from stdin
    python -m cv_agent.cli <job_id> --profile unified_jc
    python -m cv_agent.cli --paste --auto --contact swiss < posting.txt   # zero-prompt → review.json
    python -m cv_agent.cli --resume <thread_id> --approve                  # finish a paused run

The CLI seeds the state, runs the graph, and — because the two gates pause the
graph via ``interrupt()`` — drives the resume loop: it reads the pending interrupt
payload, prompts the human, and resumes with ``Command(resume=<decision>)``. See
``contracts/cli-contract.md`` for the stdout/exit-code contract.
"""

import argparse
import hashlib
import os
import re
import sys

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from core import llm
from core import paths
from core.cv_agent.graph import compile_graph
from core.profiles import ALL_PROFILES, DEFAULT_PROFILE_ID, load_active_profile
from core.storage import JobStorage


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="cv_agent",
        description="Tailor a CV (+ optional letter/recruiter message) for one job.",
    )
    p.add_argument("reference", nargs="?", help="job_id or URL (omit with --paste)")
    p.add_argument("--paste", action="store_true", help="read the posting text from stdin")
    p.add_argument("--letter", action="store_true", help="force a cover letter even if unrequested")
    p.add_argument("--profile", help="profile id (default: active profile)")
    p.add_argument("--auto", action="store_true",
                   help="run non-interactively to the approval gate (never auto-approves)")
    p.add_argument("--contact", choices=("swiss", "french"),
                   help="contact profile override (only meaningful with --auto)")
    p.add_argument("--directives", help="free-text user directives (only with --auto)")
    p.add_argument("--title", help="job title (required with --paste --auto; drives job_id + output folder)")
    p.add_argument("--company", help="company name (required with --paste --auto; drives job_id + output folder)")
    p.add_argument("--resume", metavar="THREAD_ID",
                   help="resume a run paused at the approval gate (requires --approve/--reject)")
    p.add_argument("--approve", action="store_true",
                   help="approve the resumed run (with --resume)")
    p.add_argument("--reject", metavar="NOTES",
                   help="reject the resumed run with revision notes (with --resume)")
    return p.parse_args(argv)


def _resolve_profile_id(db, override):
    if override:
        if db.get_profile(override) or override in ALL_PROFILES:
            return override
        print(f"warning: unknown profile {override!r}, using active profile", file=sys.stderr)
    return load_active_profile(db).id or DEFAULT_PROFILE_ID


def _thread_id(reference: str) -> str:
    """Deterministic checkpoint thread key: the job_id for an id entry, else a
    hash of the reference. Stable so a re-run of the same input reuses its
    checkpoint history (FR-015)."""
    if re.fullmatch(r"[0-9a-f]{20}", reference):
        return reference
    return hashlib.sha256(reference.encode()).hexdigest()[:20]


def _paste_thread_id(title: str, company: str) -> str:
    """The checkpoint thread key for a ``--paste`` entry: the job's own id.

    Mirrors ``JobPosting.id`` (key = ``{title}::{company}::paste``, no URL), so a
    paste's thread id *is* its job_id — exactly like an id entry. This replaces
    ``sha256(pasted text)``, which changed every time the posting text was re-pasted
    and could not be derived from ``--resume <job_id>`` (spec 036, US1).
    """
    key = f"{title}::{company}::paste"
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def _interrupt_payload(snapshot):
    """Extract the value handed to interrupt() from a paused snapshot."""
    for task in snapshot.tasks:
        for it in task.interrupts:
            return it.value
    return None


# ── gate prompts ────────────────────────────────────────────────────────────

def _require_field(label, current="", allow_keep=False):
    """Return a non-blank value for ``label``.

    - Blank ``current`` → prompt until a non-blank value is entered (forced input).
    - Non-blank ``current`` and ``allow_keep=False`` → return it untouched
      (fast-path for "proceed" when the field is already filled).
    - Non-blank ``current`` and ``allow_keep=True`` → prompt an override, Enter keeps it.
    """
    value = (current or "").strip()
    if value:
        if not allow_keep:
            return value
        answer = input(f"{label} override [Enter to keep]: ").strip()
        return answer or value
    while True:
        answer = input(f"{label}: ").strip()
        if answer:
            return answer
        print(f"   ⚠️ {label} is required — please enter a non-blank value.")


def prompt_analysis_gate(payload) -> dict:
    job_title = (payload.get("job_title") or "").strip()
    job_company = (payload.get("job_company") or "").strip()

    print("\n" + "=" * 64)
    print("GATE 1 — ANALYSIS")
    print("=" * 64)
    print(f"Score: {payload.get('score')} — {payload.get('score_reason') or '(no reason)'}")
    fa = payload.get("fit_analysis") or {}
    print(f"Fit:    {fa.get('fit_recap')}")
    print(f"Angle:  {fa.get('angle')}")
    print(f"Profile: {payload.get('proposed_profile')} "
          f"(confidence {payload.get('profile_confidence')})")
    if fa.get("gaps"):
        print("Gaps:   " + "; ".join(fa["gaps"]))
    print(f"Title:   {job_title or '(missing)'}")
    print(f"Company: {job_company or '(missing)'}")
    if not job_title or not job_company:
        print("\n⚠️ Title/Company missing — required before proceeding.")
    print("\n[1] proceed   [2] adjust (directives)   [3] abort")
    while True:
        choice = input("> ").strip().lower()
        if choice in ("1", "proceed", "p"):
            title = _require_field("Job title", job_title)
            company = _require_field("Company", job_company)
            out = {"decision": "proceed", "user_directives": ""}
            if title != job_title:
                out["title"] = title
            if company != job_company:
                out["company"] = company
            return out
        if choice in ("2", "adjust", "a"):
            directives = input("Directives (free text): ").strip()
            out = {"decision": "proceed", "user_directives": directives}
            profile = input("Override profile [swiss/french, Enter to keep]: ").strip().lower()
            if profile in ("swiss", "french"):
                out["proposed_profile"] = profile
            company = _require_field("Company", job_company, allow_keep=True)
            if company != job_company:
                out["company"] = company
            title = _require_field("Job title", job_title, allow_keep=True)
            if title != job_title:
                out["title"] = title
            cv_title = input("CV header title [Enter to let the agent adapt it to the role]: ").strip()
            if cv_title:
                out["title_override"] = cv_title
            return out
        if choice in ("3", "abort", "q"):
            return {"decision": "abort", "user_directives": ""}
        print("   (enter 1 / 2 / 3)")


def prompt_approval_gate(payload) -> dict:
    paths = payload.get("output_paths") or {}
    reject_count = payload.get("reject_count", 0)
    print("\n" + "=" * 64)
    print("GATE 2 — APPROVAL")
    print("=" * 64)
    print(f"PDF:  {paths.get('pdf')}")
    print(f"DOCX: {paths.get('docx')}")
    if payload.get("nextcloud_web_url"):
        print(f"NC:   {payload.get('nextcloud_web_url')}")
    print("\n[1] approve   [2] reject (notes → revise)")
    if reject_count >= 3:
        print(f"   (round {reject_count + 1} — rejected {reject_count}×; approve to finish)")
    while True:
        choice = input("> ").strip().lower()
        if choice in ("1", "approve", "y"):
            return {"approved": True, "approval_notes": ""}
        if choice in ("2", "reject", "n"):
            notes = input("Revision notes: ").strip()
            return {"approved": False, "approval_notes": notes}
        print("   (enter 1 / 2)")


# ── driver ──────────────────────────────────────────────────────────────────

def _review_path(state: dict) -> str:
    """The ``review.json`` path written by ``approval_gate`` (spec 036, US4)."""
    local_dir = (state.get("output_paths") or {}).get("local_dir", "")
    return os.path.join(local_dir, "review.json") if local_dir else ""


def run(args) -> int:
    if args.resume:
        if args.paste or args.reference:
            print("error: --resume takes no job reference", file=sys.stderr)
            return 2
        if bool(args.approve) == (args.reject is not None):  # need exactly one
            print("error: --resume requires exactly one of --approve / --reject", file=sys.stderr)
            return 2
        return _run_resume(args)
    return _run_fresh(args)


def _run_fresh(args) -> int:
    if not args.paste and not args.reference:
        print("error: provide a job_id or URL (or --paste)", file=sys.stderr)
        return 2

    title = (args.title or "").strip()
    company = (args.company or "").strip()

    if args.paste:
        reference = sys.stdin.read().strip()
        if not reference:
            print("error: --paste but no text on stdin", file=sys.stderr)
            return 2
        if args.auto and (not title or not company):
            print("error: --paste --auto requires --title and --company "
                  "(job_id and the output folder are derived from them)", file=sys.stderr)
            return 2
        if not args.auto:
            # interactive paste: collect the two fields now — they drive job_id,
            # so the analysis gate can't be the first place they are set.
            if not title:
                title = _require_field("Job title")
            if not company:
                company = _require_field("Company")
    else:
        reference = args.reference

    if not llm.is_configured():
        print("CV_AGENT_RESULT error  LLM not configured (set LLM_API_KEY / DEEPSEEK_API_KEY)",
              file=sys.stderr)
        return 3

    db = JobStorage(paths.DB_PATH)
    profile_id = _resolve_profile_id(db, args.profile)

    # The checkpoint thread key is the job's own id for a paste (derived from
    # --title/--company), else the id-entry/URL rule. Computed once so the
    # initial state and the checkpointer config can never disagree.
    thread_id = _paste_thread_id(title, company) if args.paste else _thread_id(reference)

    initial = {
        "reference": reference,
        "profile_id": profile_id,
        "force_letter": bool(args.letter),
        "thread_id": thread_id,
        "job_title": title,
        "job_company": company,
        "revision_count": 0,
        "refine_notes": [],
    }

    try:
        with SqliteSaver.from_conn_string(
            paths.data_path("cv_agent_checkpoints.sqlite")
        ) as saver:
            app = compile_graph(checkpointer=saver)
            config = {"configurable": {"thread_id": thread_id}}

            app.invoke(initial, config)

            # Resume loop: each gate pause shows up as a pending interrupt.
            while True:
                snapshot = app.get_state(config)
                if not snapshot.next:  # reached END
                    break
                node = snapshot.next[0]
                payload = _interrupt_payload(snapshot)
                if node == "analysis_gate":
                    if args.auto:
                        decision = {
                            "decision": "proceed",
                            "user_directives": args.directives or "",
                            "title_override": "",
                        }
                        if args.contact:
                            decision["proposed_profile"] = args.contact
                        app.invoke(Command(resume=decision), config)
                    else:
                        app.invoke(Command(resume=prompt_analysis_gate(payload)), config)
                elif node == "approval_gate":
                    if args.auto:
                        break  # never auto-approve; stop here for the reviewer
                    app.invoke(Command(resume=prompt_approval_gate(payload)), config)
                else:
                    print(f"CV_AGENT_RESULT error  unexpected pause at {node}", file=sys.stderr)
                    return 4

            final = app.get_state(config).values
    except KeyboardInterrupt:
        print("CV_AGENT_RESULT error  interrupted", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"CV_AGENT_RESULT error  {e}", file=sys.stderr)
        return 1

    if final.get("decision") == "abort":
        print(f"CV_AGENT_RESULT aborted   {final.get('job_id')}")
        return 0
    if args.auto:
        # review line: path + thread_id so the reviewer can --resume later.
        print(f"CV_AGENT_RESULT review    {_review_path(final)}  {final.get('thread_id')}")
        return 0
    pdf = (final.get("output_paths") or {}).get("pdf")
    print(f"CV_AGENT_RESULT ok        {pdf}")
    return 0


def _run_resume(args) -> int:
    thread_id = args.resume
    try:
        with SqliteSaver.from_conn_string(
            paths.data_path("cv_agent_checkpoints.sqlite")
        ) as saver:
            app = compile_graph(checkpointer=saver)
            config = {"configurable": {"thread_id": thread_id}}

            snapshot = app.get_state(config)
            if tuple(snapshot.next or ()) != ("approval_gate",):
                print(f"CV_AGENT_RESULT error  thread {thread_id} not paused at approval_gate",
                      file=sys.stderr)
                return 5

            decision = {"approved": bool(args.approve), "approval_notes": args.reject or ""}
            app.invoke(Command(resume=decision), config)

            # Run to the next stop: approve → END; reject → re-stop at approval_gate.
            while True:
                snapshot = app.get_state(config)
                if not snapshot.next:  # END (approved)
                    break
                node = snapshot.next[0]
                if node == "approval_gate":  # rejected → re-paused for another --resume
                    break
                print(f"CV_AGENT_RESULT error  unexpected pause at {node}", file=sys.stderr)
                return 4

            final = app.get_state(config).values
    except KeyboardInterrupt:
        print("CV_AGENT_RESULT error  interrupted", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"CV_AGENT_RESULT error  {e}", file=sys.stderr)
        return 1

    if args.approve:
        pdf = (final.get("output_paths") or {}).get("pdf")
        print(f"CV_AGENT_RESULT ok        {pdf}")
        return 0
    # reject re-stopped at approval_gate → review line again.
    print(f"CV_AGENT_RESULT review    {_review_path(final)}  {final.get('thread_id')}")
    return 0


def main(argv=None):
    return run(parse_args(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    sys.exit(main())
