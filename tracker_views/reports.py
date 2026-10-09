"""tracker_views/reports.py — Onglet Reports : candidatures (par date de candidature) + activité (spec 030)."""

from calendar import monthrange
from collections import Counter
from datetime import date

import streamlit as st

from core.export_jobs import CURRENT_STAGES, DB_PATH, rows_to_csv_bytes
from core.storage import JobStorage
from tracker_views.shared import is_active_page, md_link


def _coarse_stage(label: str) -> str:
    """Map a derived Current-stage label to its coarse bucket (FR-019 metrics)."""
    if not label:
        return "Applied"
    for stage in ("Interviewing", "Rejected", "Withdrawn", "Offer"):
        if label.startswith(stage):
            return stage
    return "Applied"


def _resolve_month(month_str: str) -> tuple[str, str]:
    """Résout 'MM/YYYY' en (date_from, date_to). Lève ValueError si invalide."""
    month, year = map(int, month_str.split("/"))
    if not (1 <= month <= 12):
        raise ValueError("mois hors bornes")
    last_day = monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"


def _preview_rows(rows: list[dict]) -> list[dict]:
    """Copy rows with the ID column rewritten to a job-detail link."""
    out = []
    for r in rows:
        pr = dict(r)
        job_id = pr.get("ID")
        if job_id:
            pr["ID"] = f"/job_detail?id={job_id}"
        out.append(pr)
    return out


def render():
    st.title("📤 Reports")

    preset = st.radio("Report", ["Applications", "Activity"], horizontal=True)
    mode = st.radio("Period", ["Month", "Date range"], horizontal=True)

    if mode == "Month":
        month_str = st.text_input("Month (MM/YYYY)", value=date.today().strftime("%m/%Y"))
    else:
        col_from, col_to = st.columns(2)
        from_date = col_from.date_input("From", value=date.today().replace(day=1), format="DD/MM/YYYY")
        to_date = col_to.date_input("To", value=date.today(), format="DD/MM/YYYY")

    stage = None
    if preset == "Applications":
        selected = st.selectbox(
            "Current stage",
            ["All stages"] + CURRENT_STAGES,
            index=0,
            help="Optional filter — rows are always selected by application date",
        )
        stage = None if selected == "All stages" else selected

    if st.button("Generate preview"):
        error = None
        date_from = date_to = mode_key = None

        if mode == "Month":
            mode_key = "month"
            try:
                date_from, date_to = _resolve_month(month_str)
            except (ValueError, AttributeError):
                error = "Invalid month format. Use MM/YYYY (e.g. 08/2026)."
        else:
            mode_key = "range"
            if from_date > to_date:
                error = "Start date must be before end date."
            else:
                date_from = from_date.strftime("%Y-%m-%d")
                date_to = to_date.strftime("%Y-%m-%d")

        if error is None:
            try:
                storage = JobStorage(str(DB_PATH))
                if preset == "Applications":
                    rows = storage.build_application_rows(date_from, date_to, current_stage=stage)
                    missing = storage.application_date_missing()
                else:
                    rows = storage.build_activity_rows(date_from, date_to)
                    missing = []
                stem = "activity" if preset == "Activity" else "applications"
                filename = f"{stem}_{date_from[:7]}.csv" if mode_key == "month" else f"{stem}_{date_from}_{date_to}.csv"
            except FileNotFoundError:
                error = f"Database not found: {DB_PATH}"

        if error:
            st.session_state["reports_generated"] = False
            st.error(error)
        else:
            st.session_state["reports_rows"] = rows
            st.session_state["reports_filename"] = filename
            st.session_state["reports_preset"] = preset
            st.session_state["reports_missing"] = missing
            st.session_state["reports_generated"] = True

    # ── Preview + download (after generation) ────────────────────────────────
    if st.session_state.get("reports_generated"):
        rows = st.session_state.get("reports_rows", [])
        filename = st.session_state.get("reports_filename", "")
        rendered_preset = st.session_state.get("reports_preset", "Applications")
        missing = st.session_state.get("reports_missing", [])

        if rendered_preset == "Applications":
            counts = Counter(_coarse_stage(r.get("Current stage", "")) for r in rows)
            c = st.columns(6)
            c[0].metric("Entries", len(rows))
            c[1].metric("Pending", counts.get("Applied", 0))
            c[2].metric("Interviewing", counts.get("Interviewing", 0))
            c[3].metric("Offer", counts.get("Offer", 0))
            c[4].metric("Rejected", counts.get("Rejected", 0))
            c[5].metric("Withdrawn", counts.get("Withdrawn", 0))

        if rows:
            st.dataframe(
                _preview_rows(rows),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "ID": st.column_config.LinkColumn("ID", display_text=r"id=(.*)"),
                    "URL": st.column_config.LinkColumn("URL"),
                },
            )
        else:
            st.info("No entries found for this period.")

        if rendered_preset == "Applications" and missing:
            with st.expander(f"Application date missing ({len(missing)})"):
                for row in missing:
                    job_id = row.get("id")
                    label = f"{row.get('company') or '—'} — {row.get('title') or '—'}"
                    st.markdown(f"- {md_link(label, f'/job_detail?id={job_id}')}")

        st.download_button(
            f"Download {filename}",
            data=rows_to_csv_bytes(rows),
            file_name=filename,
            mime="text/csv",
            disabled=not rows,
        )


if is_active_page(__file__):
    render()
