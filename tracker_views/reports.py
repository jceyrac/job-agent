"""tracker_views/reports.py — Onglet Reports : exports CSV téléchargeables via un registre de presets."""

from calendar import monthrange
from collections import Counter
from datetime import date

import streamlit as st

from export_jobs import (
    DB_PATH,
    STATUSES_ALL,
    STATUSES_DEFAULT,
    STATUS_TO_RESULTAT,
    build_export_rows,
    rows_to_csv_bytes,
)
from tracker_views.shared import is_active_page


# ── Registre de presets ──────────────────────────────────────────────────────
# label -> {"generate": callable, "help": str}
# generate(date_from, date_to, statuses, mode) -> (rows: list[dict], filename: str)


def _preset_applications(date_from, date_to, statuses, mode):
    """First preset: applications export."""
    rows = build_export_rows(DB_PATH, date_from, date_to, statuses)
    if mode == "month":
        filename = f"jobs_{date_from[:7]}.csv"
    else:
        filename = f"jobs_{date_from}_{date_to}.csv"
    return rows, filename


PRESETS = {
    "Applications": {
        "generate": _preset_applications,
    },
}


def _resolve_month(month_str: str) -> tuple[str, str]:
    """Résout 'MM/YYYY' en (date_from, date_to). Lève ValueError si invalide."""
    month, year = map(int, month_str.split("/"))
    if not (1 <= month <= 12):
        raise ValueError("mois hors bornes")
    last_day = monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"


def render():
    st.title("📤 Reports")

    # ── Controls ─────────────────────────────────────────────────────────────
    # Un seul preset (« Applications ») — pas de sélecteur de type d'export.
    preset = PRESETS["Applications"]

    mode = st.radio("Period", ["Month", "Date range"], horizontal=True)

    if mode == "Month":
        month_str = st.text_input("Month (MM/YYYY)", value=date.today().strftime("%m/%Y"))
    else:
        col_from, col_to = st.columns(2)
        from_date = col_from.date_input("From", value=date.today().replace(day=1), format="DD/MM/YYYY")
        to_date = col_to.date_input("To", value=date.today(), format="DD/MM/YYYY")

    statuses = st.multiselect("Statuses", STATUSES_ALL, default=STATUSES_DEFAULT)

    if st.button("Generate preview"):
        error = None
        date_from = date_to = mode_key = None

        if not statuses:
            error = "Select at least one status."
        elif mode == "Month":
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
                rows, filename = preset["generate"](date_from, date_to, statuses, mode_key)
            except FileNotFoundError:
                error = f"Database not found: {DB_PATH}"

        if error:
            st.session_state["reports_generated"] = False
            st.error(error)
        else:
            st.session_state["reports_rows"] = rows
            st.session_state["reports_filename"] = filename
            st.session_state["reports_generated"] = True

    # ── Preview + download (after generation) ────────────────────────────────
    if st.session_state.get("reports_generated"):
        rows = st.session_state.get("reports_rows", [])
        filename = st.session_state.get("reports_filename", "")

        counts = Counter(
            STATUS_TO_RESULTAT.get(r.get("Status", ""), "en suspens") for r in rows
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Entries", len(rows))
        c2.metric("Pending", counts.get("en suspens", 0))
        c3.metric("Negative", counts.get("négatif", 0))

        preview = [
            {
                "Date": r.get("Date", ""),
                "Company": r.get("Entreprise / Adresse", ""),
                "Title": r.get("Description du poste", ""),
                "Status": r.get("Status", ""),
                "ID": f"/job_detail?id={r.get('ID', '')}",
            }
            for r in rows
        ]
        st.dataframe(
            preview,
            use_container_width=True,
            hide_index=True,
            column_config={
                "ID": st.column_config.LinkColumn("ID", display_text=r"id=(.*)"),
            },
        )

        st.download_button(
            f"Download {filename}",
            data=rows_to_csv_bytes(rows),
            file_name=filename,
            mime="text/csv",
            disabled=not rows,
        )


if is_active_page(__file__):
    render()
