"""tracker_views/task_progress.py — queue-backed run progress (spec 043, US4).

Shared by ``jobs.py`` and ``settings.py``: each launch button enqueues a task
through ``JobStorage`` and renders its progress here. No subprocess, no threads,
survives a reload (resume is via ``get_active_task``, not session state), and
dedups through the partial unique index. The tracker keeps calling ``JobStorage``
directly (non-goal: calling the API).
"""
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from core.tasks import WORKER_HEARTBEAT_STALE_SECONDS

# Human labels for the four task types (the "current task labels" the approved
# mockup refers to).
_TASK_LABELS = {
    "scrape": "Scrape",
    "scrape_monitored": "Monitoring",
    "extract": "Re-extract",
    "score": "Score",
}

# Number of output lines shown in the live / last-run tail.
_TAIL_LINES = 40


def task_label(type: str) -> str:
    """Human label for a task type, falling back to the title-cased type."""
    return _TASK_LABELS.get(type, type.title())


def _session_key(type: str, params: dict | None) -> str:
    """Stable per-button session key for the last-enqueued task id.

    Used only to keep the terminal result visible after a task finishes (the
    dedup index drops finished rows, so ``get_active_task`` no longer finds
    them). Resume of a live task never depends on this — it goes through
    ``get_active_task``.
    """
    suffix = "_".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    return f"task_{type}" + (f"_{suffix}" if suffix else "")


def enqueue(db, type: str, params: dict | None = None) -> None:
    """Enqueue a task for this ``(type, params)`` and rerun the page.

    ``enqueue_task`` dedups: if an identical task is already queued or running,
    it returns the existing row (no duplicate run). We remember that id so the
    final result stays visible once the task reaches a terminal status.
    """
    task = db.enqueue_task(type, params, requested_by="tracker")
    st.session_state[_session_key(type, params)] = task["id"]
    st.rerun()


def _heartbeat_stale(db) -> bool:
    """True when the worker has not written a heartbeat recently (or ever)."""
    ts = db.get_worker_heartbeat()
    if not ts:
        return True
    try:
        beat = datetime.fromisoformat(ts)
    except ValueError:
        return True
    age = (datetime.now(timezone.utc) - beat).total_seconds()
    return age > WORKER_HEARTBEAT_STALE_SECONDS


def _fmt_elapsed(started_at: str | None) -> str:
    """Elapsed time since ``started_at`` as ``<n>s`` / ``<m>m <s>s`` / ``<h>h …``."""
    if not started_at:
        return ""
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return ""
    s = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m {sec}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m {sec}s"


def _running_other(db, task_id: str) -> dict | None:
    """The task currently ``running`` that is not ``task_id``, if any."""
    running, _ = db.list_tasks(status="running", limit=5)
    return next((t for t in running if t["id"] != task_id), None)


def _render_tail(db, task_id: str, title: str) -> None:
    """Show the last ``_TAIL_LINES`` output lines in an expander."""
    events = db.get_task_events(task_id)
    if not events:
        return
    lines = [e["line"] for e in events[-_TAIL_LINES:]]
    with st.expander(title, expanded=False):
        st.text("\n".join(lines))


def _render(db, task: dict) -> None:
    type = task["type"]
    status = task["status"]
    label = task_label(type)
    started_at = task.get("started_at")

    if status == "queued":
        holder = db.get_pipeline_lock_holder()
        if holder == "cron":
            st.info(
                "The scheduled pipeline (07:00, 11:00, 16:00) is running — "
                "your task starts when it finishes."
            )
        elif (other := _running_other(db, task["id"])) is not None:
            st.info(f'queued · waiting for "{task_label(other["type"])}" to finish')
        elif _heartbeat_stale(db):
            st.warning("waiting for worker")
        else:
            st.info(f"⏳ {label} — queued")
        if st.button("⏹ Cancel", key=f"cancel_{task['id']}"):
            db.mark_cancelled(task["id"])
            st.rerun()
        return

    if status == "running":
        elapsed = _fmt_elapsed(started_at)
        st.info(f"⏳ {label} — running · {elapsed}" if elapsed else f"⏳ {label} — running")
        _render_tail(db, task["id"], "Live output")
        c1, c2 = st.columns([1, 3])
        with c1:
            if st.button("⏹ Stop", key=f"stop_{task['id']}"):
                db.request_cancel(task["id"])
                st.rerun()
        with c2:
            if st.button("🔄 Refresh", key=f"refresh_{task['id']}"):
                st.rerun()
        return

    if status == "succeeded":
        st.success(f"✅ {label} completed successfully!")
        _render_tail(db, task["id"], "Last run output")
        return

    if status == "failed":
        st.error(f"❌ {label} failed")
        if task.get("error"):
            with st.expander("Error", expanded=False):
                st.text(task["error"])
        _render_tail(db, task["id"], "Last run output")
        return

    if status == "cancelled":
        if started_at:
            st.warning(f"⏹ {label} stopped — results saved before the stop are kept.")
        else:
            st.warning(f"⏹ {label} cancelled")
        return


def render_task_progress(db, type: str, params: dict | None = None) -> None:
    """Render the queue progress for a launch site, if there is a task to show.

    Live tasks are found via ``get_active_task`` (the dedup row) so a reload
    resumes them; a finished task is shown from the id the last ``enqueue``
    remembered in session state.
    """
    key = _session_key(type, params)
    task = db.get_active_task(type, params)
    if task is None:
        last_id = st.session_state.get(key)
        task = db.get_task(last_id) if last_id else None
    else:
        st.session_state[key] = task["id"]
    if task is None:
        return
    _render(db, task)
