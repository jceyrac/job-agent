"""cli.py — the human driver loop for the CV agent (spec 029).

Run::

    python -m cv_agent.cli <job_id>            # existing scored job
    python -m cv_agent.cli <url>               # live careers/ATS URL
    python -m cv_agent.cli --paste --letter    # pasted posting text from stdin
    python -m cv_agent.cli <job_id> --profile unified_jc

The CLI seeds the state, runs the graph, and — because the two gates pause the
graph via ``interrupt()`` — drives the resume loop: it reads the pending interrupt
payload, prompts the human, and resumes with ``Command(resume=<decision>)``. See
``contracts/cli-contract.md`` for the stdout/exit-code contract.
"""

import argparse
import hashlib
import re
import sys

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

import llm
import paths
from cv_agent.graph import compile_graph
from profiles import ALL_PROFILES, DEFAULT_PROFILE_ID, load_active_profile
from storage import JobStorage


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="cv_agent",
        description="Tailor a CV (+ optional letter/recruiter message) for one job.",
    )
    p.add_argument("reference", nargs="?", help="job_id or URL (omit with --paste)")
    p.add_argument("--paste", action="store_true", help="read the posting text from stdin")
    p.add_argument("--letter", action="store_true", help="force a cover letter even if unrequested")
    p.add_argument("--profile", help="profile id (default: active profile)")
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

def run(args) -> int:
    if not args.paste and not args.reference:
        print("error: provide a job_id or URL (or --paste)", file=sys.stderr)
        return 2
    if not llm.is_configured():
        print("CV_AGENT_RESULT error  LLM not configured (set LLM_API_KEY / DEEPSEEK_API_KEY)",
              file=sys.stderr)
        return 3

    if args.paste:
        reference = sys.stdin.read().strip()
        if not reference:
            print("error: --paste but no text on stdin", file=sys.stderr)
            return 2
    else:
        reference = args.reference

    db = JobStorage(paths.DB_PATH)
    profile_id = _resolve_profile_id(db, args.profile)

    initial = {
        "reference": reference,
        "profile_id": profile_id,
        "force_letter": bool(args.letter),
        "revision_count": 0,
        "refine_notes": [],
    }

    try:
        with SqliteSaver.from_conn_string(
            paths.data_path("cv_agent_checkpoints.sqlite")
        ) as saver:
            app = compile_graph(checkpointer=saver)
            config = {"configurable": {"thread_id": _thread_id(reference)}}

            app.invoke(initial, config)

            # Resume loop: each gate pause shows up as a pending interrupt.
            while True:
                snapshot = app.get_state(config)
                if not snapshot.next:  # reached END
                    break
                node = snapshot.next[0]
                payload = _interrupt_payload(snapshot)
                if node == "analysis_gate":
                    app.invoke(Command(resume=prompt_analysis_gate(payload)), config)
                elif node == "approval_gate":
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
    pdf = (final.get("output_paths") or {}).get("pdf")
    print(f"CV_AGENT_RESULT ok        {pdf}")
    return 0


def main(argv=None):
    return run(parse_args(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    sys.exit(main())
