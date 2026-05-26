# Build plan — Companies page filters + UX polish

One Claude Code prompt. Self-contained.

Repo root: `/Users/jeanclaudevd/AI-Suite/job_agent`
DB path: `data/jobs.db`

Context (verified against current DB, 1,445 companies):
- Distinct countries: top 10 are UK (198), FR (175), DE (173), CH (143),
  NL (122), ES (74), unknown (71), US (62), BE (60), IE (53).
- Distinct sectors: tech_saas (604), fintech (244), other (90), healthcare
  (72), e_commerce (67), web3_crypto (66), manufacturing (59), retail (50),
  ai_ml (44), energy (34).
- Distinct sizes: large (592), scaleup (493), sme (152), unknown (115),
  startup (70), NULL (23).
- The existing Companies page only filters by status, name search, and
  exclude_blacklisted. The user wants location, industry, size, plus a
  cleaner click target on each row.

---

## Prompt — Filters + UX fixes for the Companies list

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent
Files: tracker_views/companies.py, tracker_views/shared.py, storage.py

GOAL
Add country, sector, size, job-count, and last-interaction filters to the
Companies list. Replace the "View" button on each row with a clickable
company name (matches the cards pattern in jobs.py). Add sort options.

INVESTIGATION FIRST
1. Read tracker_views/companies.py — current sidebar has only:
   exclude_blacklisted, status, search.
2. Read storage.py:get_companies (around line 1775) — already returns
   c.company_country, c.industry_sector, c.company_size, job_count,
   contact_count, last_interaction_at. SQL filtering can hook in cleanly.
3. Read tracker_views/shared.py for the existing load_companies cached
   wrapper around get_companies — we'll need to extend its signature.

EXTEND storage.get_companies
Add four new kwargs (all optional, all default None to preserve existing
callers):

    def get_companies(
        self,
        *,
        status: list[str] | None = None,
        search: str | None = None,
        exclude_blacklisted: bool = True,
        countries: list[str] | None = None,
        sectors: list[str] | None = None,
        sizes: list[str] | None = None,
        min_job_count: int | None = None,
        last_interaction_within_days: int | None = None,
    ) -> list[dict]:

Implementation:
- For countries / sectors / sizes: build WHERE clauses with IN (?, ?, ...)
  and extend params. Treat None/empty as "no filter".
- For min_job_count: add `HAVING job_count >= ?` to the existing GROUP BY
  block (job_count is from the subquery — check whether it's in HAVING
  range or needs to wrap in an outer query; if needed, add a final
  `WHERE job_count >= ?` after the SELECT-with-joins).
- For last_interaction_within_days: when set, only include companies
  where last_interaction_at is within that many days. NULL last_interaction_at
  should NEVER pass this filter (i.e. "interacted recently" means there
  must be an interaction at all).
- A special value handled by the UI (not by storage): "never interacted"
  → companies with last_interaction_at IS NULL. We'll route this through
  Python-side filtering in load_companies, not SQL.

EXTEND tracker_views/shared.py load_companies
Update the cached wrapper to accept and forward the new kwargs:

    @st.cache_data(ttl=60, show_spinner=False)
    def load_companies(
        status: list[str] | None = None,
        search: str | None = None,
        exclude_blacklisted: bool = True,
        countries: tuple[str, ...] = (),
        sectors: tuple[str, ...] = (),
        sizes: tuple[str, ...] = (),
        min_job_count: int | None = None,
        last_interaction_within_days: int | None = None,
    ) -> list[dict]:
        return get_db().get_companies(
            status=status, search=search,
            exclude_blacklisted=exclude_blacklisted,
            countries=list(countries) or None,
            sectors=list(sectors) or None,
            sizes=list(sizes) or None,
            min_job_count=min_job_count,
            last_interaction_within_days=last_interaction_within_days,
        )

Use tuples for the multiselect args so the cache key is hashable.

REWRITE THE SIDEBAR IN tracker_views/companies.py
Replace the existing sidebar block with:

    with st.sidebar:
        st.markdown("### 🔍 Filters")

        exclude_bl = st.checkbox("Exclude blacklisted", value=True,
                                  key="co_exclude_bl")
        status_filter = st.multiselect(
            "Status", COMPANY_STATUSES, key="co_status",
            default=[s for s in COMPANY_STATUSES if s != "blacklisted"],
        )
        search = st.text_input("Search by name", key="co_search")

        st.markdown("---")
        st.markdown("**Geography & industry**")
        country_filter = st.multiselect(
            "Country", COUNTRY_OPTIONS + ["unknown"], key="co_country",
            format_func=lambda c: f"{COUNTRY_FLAG.get(c, '🌐')} {c}",
        )
        sector_filter = st.multiselect(
            "Sector", list(SECTOR_LABELS.values()), key="co_sector",
            format_func=lambda c: sector_label(c),
        )
        size_filter = st.multiselect(
            "Size", ["startup", "sme", "scaleup", "large", "unknown"],
            key="co_size",
        )

        st.markdown("---")
        st.markdown("**Activity**")
        min_jobs = st.number_input(
            "Min jobs at this company", min_value=0, max_value=50,
            value=0, step=1, key="co_min_jobs",
        )
        last_ix_choice = st.selectbox(
            "Last interaction",
            ["Any", "Within 1 week", "Within 1 month",
             "Within 3 months", "Never interacted"],
            key="co_last_ix",
        )
        sort_by = st.selectbox(
            "Sort by",
            ["Name (A-Z)", "Job count (desc)", "Contact count (desc)",
             "Last interaction (recent first)", "Status"],
            key="co_sort",
        )

Map last_ix_choice → kwargs:

    _LAST_IX_DAYS = {
        "Within 1 week": 7,
        "Within 1 month": 30,
        "Within 3 months": 90,
    }
    last_ix_days = _LAST_IX_DAYS.get(last_ix_choice)
    only_never = (last_ix_choice == "Never interacted")

Load companies with the filters applied:

    companies = load_companies(
        status=status_filter if status_filter else None,
        search=search if search else None,
        exclude_blacklisted=exclude_bl,
        countries=tuple(country_filter),
        sectors=tuple(sector_filter),
        sizes=tuple(size_filter),
        min_job_count=int(min_jobs) if min_jobs else None,
        last_interaction_within_days=last_ix_days,
    )

After SQL, apply the "never interacted" filter Python-side:

    if only_never:
        companies = [c for c in companies if not c.get("last_interaction_at")]

Then sort:

    _SORTERS = {
        "Name (A-Z)":
            lambda c: (c.get("name") or "").lower(),
        "Job count (desc)":
            lambda c: -(c.get("job_count") or 0),
        "Contact count (desc)":
            lambda c: -(c.get("contact_count") or 0),
        "Last interaction (recent first)":
            lambda c: -(_parse_dt(c.get("last_interaction_at"))),
        "Status":
            lambda c: c.get("status") or "",
    }

`_parse_dt` should return 0 when the value is None and a sortable int (e.g.
`int(datetime.fromisoformat(...).timestamp())`) otherwise. Add it as a
helper at the top of the file.

    companies.sort(key=_SORTERS[sort_by])

UX FIX — REPLACE "View" BUTTON WITH CLICKABLE NAME
In the existing card-rendering loop, replace:

    with c1:
        st.markdown(f"**{c['name']}**")
        ...
    with c3:
        st.markdown(md_link("View", f"/company_detail?id={c['id']}"))

with:

    with c1:
        st.markdown(md_link(f"**{c['name']}**",
                            f"/company_detail?id={c['id']}"))
        ...
    # c3 can either become a small button column for future bulk-select
    # checkboxes, or be dropped if currently unused.

If you drop c3, change `c1, c2, c3 = st.columns([4, 3, 1])` to
`c1, c2 = st.columns([5, 3])`.

ALSO — STATS CAPTION
Above the "Bulk status change" expander, the current caption reads
`f"{len(companies)} companies"`. Update it to show:
    f"{len(companies)} companies · "
    f"{sum(c.get('job_count', 0) for c in companies)} open jobs across them"

This gives the user a sense of prospecting depth at a glance.

CONSTRAINTS
- Don't touch tracker_views/company_detail.py — only the list view.
- Don't break tracker_views/dashboard.py, which also calls load_companies
  with default arguments. The new kwargs all default to None/empty, so
  existing callers stay valid.
- Don't introduce new dependencies. stdlib only.
- Test the cache key: load_companies with the same filter set called twice
  in one render should NOT trigger two SQL calls. Tuples in the signature
  keep it hashable.

VERIFY
1. `streamlit run tracker.py` — open Companies. New sidebar groups appear
   in this order: Filters / Geography & industry / Activity.
2. Pick Country = Switzerland — list shrinks to the 143 CH companies.
3. Add Sector = web3_crypto — list shrinks further to CH × web3 companies.
4. Set "Min jobs at this company" = 2 — only multi-posting companies remain.
5. Set "Last interaction" = "Never interacted" — only companies you've
   never logged an interaction against appear.
6. Sort by "Job count (desc)" — top of list has the highest job_count.
7. Click a company name — navigates to /company_detail?id=<id>.
8. The stats caption now reads "<N> companies · <M> open jobs across them".
9. dashboard.py still renders (no regression from the load_companies
   signature change).
```

---

## Run notes

This is independent of the preference report (different scope, different file). Safe to run either before or after `BUILD_PREFERENCE_REPORT.md`. A quick 1-prompt change — should land in one Claude Code session.
