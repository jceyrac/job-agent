"""tracker_views/onboarding.py — Multi-step onboarding wizard for new users.

Flow: Template → Questionnaire → CV → Generate → Review/Refine → Save
"""

import sys
import streamlit as st

from tracker_views.shared import get_db, set_secret, env_is_set, SECTOR_LABELS, COUNTRY_OPTIONS

# ── Template defaults ─────────────────────────────────────────────────────
# Each template pre-fills the questionnaire dict.  Empty fields use the
# QUESTIONNAIRE CONTRACT defaults (see profile_generator.py).

TEMPLATES = {
    "General tech / product": {
        "target_titles": ["product manager", "product owner"],
        "seniority": "senior",
        "locations": [],
        "base_country": "",
        "work_modes": ["remote", "hybrid"],
        "onsite_country_ok": [],
        "company_sizes": ["startup", "scaleup", "sme"],
        "favored_industries": [],
        "avoided_industries": [],
        "work_languages": ["english"],
        "avoid_language_required": [],
        "remote_only_foreign": True,
        "banned_countries": [],
        "salary_floor": "",
        "dealbreakers": "",
        "extra_notes": "",
    },
    "Fintech": {
        "target_titles": ["product manager", "product owner"],
        "seniority": "senior",
        "locations": [],
        "base_country": "",
        "work_modes": ["remote", "hybrid"],
        "onsite_country_ok": [],
        "company_sizes": ["startup", "scaleup", "sme"],
        "favored_industries": ["fintech", "payments", "wealthtech", "regtech", "embedded finance"],
        "avoided_industries": [],
        "work_languages": ["english"],
        "avoid_language_required": [],
        "remote_only_foreign": True,
        "banned_countries": [],
        "salary_floor": "",
        "dealbreakers": "",
        "extra_notes": "",
    },
    "Web3 / Crypto": {
        "target_titles": ["product manager", "product owner"],
        "seniority": "senior",
        "locations": [],
        "base_country": "",
        "work_modes": ["remote"],
        "onsite_country_ok": [],
        "company_sizes": ["startup", "scaleup"],
        "favored_industries": ["web3_crypto", "defi", "fintech", "blockchain"],
        "avoided_industries": [],
        "work_languages": ["english"],
        "avoid_language_required": [],
        "remote_only_foreign": True,
        "banned_countries": [],
        "salary_floor": "",
        "dealbreakers": "",
        "extra_notes": "",
    },
    "Custom (blank)": {
        "target_titles": [],
        "seniority": "any",
        "locations": [],
        "base_country": "",
        "work_modes": ["remote"],
        "onsite_country_ok": [],
        "company_sizes": [],
        "favored_industries": [],
        "avoided_industries": [],
        "work_languages": ["english"],
        "avoid_language_required": [],
        "remote_only_foreign": True,
        "banned_countries": [],
        "salary_floor": "",
        "dealbreakers": "",
        "extra_notes": "",
    },
}

WORK_MODE_OPTIONS = ["remote", "hybrid", "on-site"]
SENIORITY_OPTIONS = ["any", "mid", "senior", "lead", "junior"]
COMPANY_SIZE_OPTIONS = ["startup", "scaleup", "sme", "large"]
LANGUAGE_OPTIONS = [
    "english", "french", "german", "spanish", "italian", "portuguese",
    "dutch", "swedish", "danish", "norwegian", "finnish", "polish",
    "czech", "hungarian", "romanian", "greek", "turkish", "russian",
    "mandarin", "japanese", "korean", "arabic", "hebrew",
]


def _back_button(step: int) -> None:
    """Render a back button that decrements the step."""
    if st.button("← Back", key=f"back_{step}"):
        st.session_state["onboarding_step"] = step - 1
        st.rerun()


# ── Step renderers ──────────────────────────────────────────────────────────

def _render_welcome():
    st.title("🚀 Welcome to Job Agent")
    st.markdown(
        "Let's set up your job search in a few minutes.  You'll answer "
        "a short questionnaire, optionally upload your CV, and the system "
        "will generate a personalised scoring rubric that evaluates every "
        "job posting against *your* criteria."
    )

    template = st.radio(
        "Pick a starting template:",
        list(TEMPLATES.keys()),
        index=0,
    )

    st.caption(
        "Templates only pre-fill the questionnaire below — you can edit "
        "everything before generating your profile."
    )

    if st.button("Start →", use_container_width=True, type="primary"):
        st.session_state["q"] = dict(TEMPLATES[template])  # shallow copy
        st.session_state["onboarding_step"] = 1
        st.session_state["cv_text"] = ""
        st.rerun()


def _render_questionnaire():
    st.title("📋 Your search criteria")
    st.caption("These answers drive both the search queries and the scoring rubric.")

    q: dict = st.session_state.get("q", TEMPLATES["General tech / product"])

    with st.form("onboarding_q"):
        st.subheader("Role")
        c1, c2 = st.columns(2)
        with c1:
            target_titles = st.text_input(
                "Target job titles (comma-separated)",
                value=", ".join(q.get("target_titles", [])),
                placeholder="product manager, product owner, head of product",
            )
        with c2:
            seniority = st.selectbox(
                "Seniority level",
                SENIORITY_OPTIONS,
                index=SENIORITY_OPTIONS.index(q.get("seniority", "any")),
            )

        st.subheader("Location")
        c1, c2 = st.columns(2)
        with c1:
            base_country = st.selectbox(
                "Your base country (where you live / are authorised to work)",
                [""] + COUNTRY_OPTIONS,
                index=([""] + COUNTRY_OPTIONS).index(q.get("base_country", ""))
                if q.get("base_country", "") in COUNTRY_OPTIONS else 0,
            )
            locations = st.multiselect(
                "Preferred locations",
                COUNTRY_OPTIONS + ["Remote (anywhere)"],
                default=[l for l in q.get("locations", []) if l in COUNTRY_OPTIONS + ["Remote (anywhere)"]],
            )
        with c2:
            work_modes = st.multiselect(
                "Work modes you accept",
                WORK_MODE_OPTIONS,
                default=[w for w in q.get("work_modes", []) if w in WORK_MODE_OPTIONS],
            )
            onsite_country_ok = st.multiselect(
                "Countries where on-site / hybrid is acceptable",
                COUNTRY_OPTIONS,
                default=[c for c in q.get("onsite_country_ok", []) if c in COUNTRY_OPTIONS],
            )

        st.subheader("Company")
        c1, c2 = st.columns(2)
        with c1:
            company_sizes = st.multiselect(
                "Preferred company sizes",
                COMPANY_SIZE_OPTIONS,
                default=[s for s in q.get("company_sizes", []) if s in COMPANY_SIZE_OPTIONS],
            )
        with c2:
            remote_only_foreign = st.checkbox(
                "Foreign employers only if fully remote",
                value=q.get("remote_only_foreign", True),
            )

        st.subheader("Industries")
        c1, c2 = st.columns(2)
        sector_keys = list(SECTOR_LABELS.keys())
        with c1:
            favored_labels = st.multiselect(
                "Favoured industries",
                sector_keys,
                default=[k for k, v in SECTOR_LABELS.items()
                         if v in q.get("favored_industries", [])],
            )
        with c2:
            avoided_labels = st.multiselect(
                "Avoided industries",
                sector_keys,
                default=[k for k, v in SECTOR_LABELS.items()
                         if v in q.get("avoided_industries", [])],
            )

        st.subheader("Languages")
        c1, c2 = st.columns(2)
        with c1:
            work_languages = st.multiselect(
                "Languages you work in professionally",
                LANGUAGE_OPTIONS,
                default=[l for l in q.get("work_languages", []) if l in LANGUAGE_OPTIONS],
            )
        with c2:
            avoid_language_required = st.multiselect(
                "Avoid jobs requiring these languages",
                LANGUAGE_OPTIONS,
                default=[l for l in q.get("avoid_language_required", []) if l in LANGUAGE_OPTIONS],
            )

        st.subheader("Hard exclusions")
        banned_countries = st.multiselect(
            "Banned countries (hard-reject any job based here)",
            COUNTRY_OPTIONS,
            default=[c for c in q.get("banned_countries", []) if c in COUNTRY_OPTIONS],
        )

        st.subheader("Optional details")
        salary_floor = st.text_input(
            "Salary floor (free text — e.g. 'CHF 140k' or '$150k USD')",
            value=q.get("salary_floor", ""),
        )
        dealbreakers = st.text_area(
            "Dealbreakers (anything that should cap the score at 3)",
            value=q.get("dealbreakers", ""),
            height=80,
        )
        extra_notes = st.text_area(
            "Extra notes for the profile generator (anything else you want the scorer to know)",
            value=q.get("extra_notes", ""),
            height=80,
        )

        submitted = st.form_submit_button("Next →", use_container_width=True, type="primary")
        if submitted:
            titles_list = [t.strip() for t in target_titles.split(",") if t.strip()]
            if not titles_list:
                st.error("Please enter at least one target job title.")
                return
            st.session_state["q"] = {
                "target_titles": titles_list,
                "seniority": seniority,
                "locations": locations,
                "base_country": base_country,
                "work_modes": work_modes,
                "onsite_country_ok": onsite_country_ok,
                "company_sizes": company_sizes,
                "favored_industries": [SECTOR_LABELS[k] for k in favored_labels],
                "avoided_industries": [SECTOR_LABELS[k] for k in avoided_labels],
                "work_languages": work_languages,
                "avoid_language_required": avoid_language_required,
                "remote_only_foreign": remote_only_foreign,
                "banned_countries": banned_countries,
                "salary_floor": salary_floor,
                "dealbreakers": dealbreakers,
                "extra_notes": extra_notes,
            }
            st.session_state["onboarding_step"] = 2
            st.rerun()

    _back_button(1)


def _render_cv():
    st.title("📄 Your CV / resume")
    st.caption(
        "Upload your CV for better scoring-context generation. "
        "The text is sent to the LLM so it can reference your actual experience. "
        "You can skip this step — the rubric will be less personalised."
    )

    db = get_db()

    # File upload
    uploaded = st.file_uploader(
        "Upload CV (docx, pdf, txt, md)",
        type=["docx", "pdf", "txt", "md"],
        key="cv_uploader",
    )
    if uploaded is not None:
        from cv_extract import extract_cv_text_from_bytes, save_uploaded_cv
        data = uploaded.read()
        text = extract_cv_text_from_bytes(data, uploaded.name)
        if text:
            save_uploaded_cv(data, uploaded.name, db)
            st.session_state["cv_text"] = text
            st.success(f"Extracted {len(text):,} characters from {uploaded.name}.")
            st.text_area("Preview (first 800 chars)", text[:800], height=150, disabled=True)
        else:
            st.warning("Could not extract text from this file. Try pasting below.")

    # Paste fallback
    st.divider()
    st.caption("Or paste your CV text:")
    pasted = st.text_area(
        "CV text", value=st.session_state.get("cv_text", ""),
        height=200,
        placeholder="Senior Product Manager with X years of experience...",
        key="cv_paste",
    )
    if pasted:
        st.session_state["cv_text"] = pasted

    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("Skip", use_container_width=True):
            st.session_state["onboarding_step"] = 3
            st.rerun()
    with c2:
        if st.button("Next →", use_container_width=True, type="primary"):
            st.session_state["onboarding_step"] = 3
            st.rerun()

    _back_button(2)


def _render_generate():
    st.title("✨ Generate your profile")

    q = st.session_state.get("q", {})
    cv_text = st.session_state.get("cv_text", "")

    st.markdown("**Summary of your answers:**")
    st.caption(
        f"**Roles:** {', '.join(q.get('target_titles', []))}  |  "
        f"**Seniority:** {q.get('seniority', 'any')}  |  "
        f"**Base:** {q.get('base_country', 'not set')}  |  "
        f"**Work modes:** {', '.join(q.get('work_modes', []))}  |  "
        f"**Industries:** {', '.join(q.get('favored_industries', [])) or 'none specified'}"
    )
    if cv_text:
        st.caption(f"CV: {len(cv_text):,} characters of text extracted")

    # Guard: GROQ_API_KEY must be set — offer inline entry (Settings is
    # unreachable during first-run gating, so the user must set it here).
    if not env_is_set("GROQ_API_KEY"):
        st.info(
            "To write your scoring rubric, the app needs a free Groq API key. "
            "It's used only on your machine."
        )
        st.markdown(
            "Create a free account (no credit card) and generate a key at "
            "[console.groq.com/keys](https://console.groq.com/keys) — it starts "
            "with `gsk_`."
        )
        new_key = st.text_input("Groq API key", type="password",
                                placeholder="gsk_…", label_visibility="collapsed")
        c1, c2 = st.columns([1, 4])
        with c1:
            if st.button("Save key", type="primary"):
                if new_key.strip():
                    set_secret("GROQ_API_KEY", new_key)
                    st.success("Key saved.")
                    st.rerun()
                else:
                    st.warning("Paste a key first.")
        _back_button(3)
        return   # do not show Generate until the key is set

    if "generated_criteria" not in st.session_state:
        if st.button("✨ Generate my profile", use_container_width=True, type="primary"):
            with st.spinner("Writing your scoring rubric… this takes ~30–60 seconds."):
                try:
                    from profile_generator import generate_profile
                    criteria = generate_profile(q, cv_text)
                    st.session_state["generated_criteria"] = criteria
                    st.session_state["onboarding_step"] = 4
                    st.rerun()
                except Exception as e:
                    msg = str(e)
                    # Distinguish auth failures from quota/other errors
                    if any(token in msg.lower() for token in ("401", "invalid", "api key", "unauthorized")):
                        st.error(
                            f"Generation failed: {msg}\n\n"
                            "That key didn't work — re-enter it below."
                        )
                        st.rerun()
                    else:
                        st.error(
                            f"Generation failed: {msg}\n\n"
                            "This is usually a temporary quota issue. Try again."
                        )
                        if st.button("Retry"):
                            st.rerun()
    else:
        st.success("Profile generated!  Proceed to review →")
        if st.button("Review →", use_container_width=True, type="primary"):
            st.session_state["onboarding_step"] = 4
            st.rerun()

    _back_button(3)


def _render_review():
    st.title("🔍 Review & refine")

    criteria = st.session_state.get("generated_criteria", {})
    q = st.session_state.get("q", {})

    if not criteria:
        st.warning("No generated profile.  Go back and generate first.")
        _back_button(4)
        return

    # ── scoring_context editor ──────────────────────────────────────────
    st.subheader("Scoring rubric")
    st.caption("This is the essay the scorer reads.  Edit it freely.")

    ctx = st.text_area(
        "scoring_context",
        value=criteria.get("scoring_context", ""),
        height=500,
        key="review_context",
        label_visibility="collapsed",
    )

    # ── Refinement ──────────────────────────────────────────────────────
    st.caption("Refine with a plain-language instruction:")
    c1, c2 = st.columns([3, 1])
    with c1:
        instruction = st.text_input(
            "Refine instruction",
            placeholder="e.g. 'Make the language stricter for large corporates' or 'Remove the German-language exclusion'",
            label_visibility="collapsed",
            key="refine_input",
        )
    with c2:
        if st.button("Refine", use_container_width=True) and instruction.strip():
            with st.spinner("Refining…"):
                from profile_generator import refine_scoring_context
                try:
                    new_ctx = refine_scoring_context(ctx, instruction.strip(), q)
                    criteria["scoring_context"] = new_ctx
                    st.session_state["generated_criteria"] = criteria
                    st.success("Refined.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Refinement failed: {e}")

    # Update criteria with edited context
    criteria["scoring_context"] = ctx
    st.session_state["generated_criteria"] = criteria

    # ── Structured fields quick-edit ────────────────────────────────────
    with st.expander("Edit structured fields"):
        c1, c2 = st.columns(2)
        with c1:
            new_search_locations = st.text_area(
                "Search locations (one per line)",
                value="\n".join(criteria.get("search_locations", [])),
                height=100,
            )
            new_banned = st.text_area(
                "Banned countries (one per line)",
                value="\n".join(criteria.get("banned_countries", [])),
                height=100,
            )
        with c2:
            new_excluded_langs = st.text_area(
                "Excluded languages (one per line)",
                value="\n".join(criteria.get("excluded_languages", [])),
                height=100,
            )
        # Apply edits
        criteria["search_locations"] = [x.strip() for x in new_search_locations.split("\n") if x.strip()]
        criteria["banned_countries"] = [x.strip() for x in new_banned.split("\n") if x.strip()]
        criteria["excluded_languages"] = [x.strip() for x in new_excluded_langs.split("\n") if x.strip()]
        st.session_state["generated_criteria"] = criteria

    # ── Navigation ──────────────────────────────────────────────────────
    c1, c2 = st.columns([1, 3])
    with c1:
        _back_button(4)
    with c2:
        if st.button("Save profile →", use_container_width=True, type="primary"):
            st.session_state["onboarding_step"] = 5
            st.rerun()


def _render_save():
    st.title("💾 Save your profile")

    criteria = st.session_state.get("generated_criteria", {})
    if not criteria:
        st.warning("No generated profile.  Go back and generate first.")
        _back_button(5)
        return

    db = get_db()
    from profiles import SearchProfile, DEFAULT_PROFILE_ID

    active_id = db.get_config("active_profile_id", DEFAULT_PROFILE_ID)
    # Use the name from the existing profile or default
    existing = db.get_profile(active_id)
    name = (existing.get("name") if existing else None) or "My Profile"

    new_name = st.text_input("Profile name", value=name)

    if st.button("💾 Save & finish", use_container_width=True, type="primary"):
        profile = SearchProfile.from_criteria(active_id, new_name, criteria)
        db.upsert_profile(profile)
        db.set_config("active_profile_id", profile.id)
        db.set_config("onboarding_complete", "true")
        st.balloons()
        st.success(f"Profile **{new_name}** saved!  All future scrape/score runs will use it.")
        st.caption("Redirecting to Dashboard…")
        # Clear session state so re-run starts fresh
        for key in ["onboarding_step", "q", "cv_text", "generated_criteria"]:
            st.session_state.pop(key, None)
        st.cache_data.clear()
        st.switch_page("tracker_views/dashboard.py")

    _back_button(5)


# ══════════════════════════════════════════════════════════════════════════════
# Page entry point
# ══════════════════════════════════════════════════════════════════════════════

def render():
    # Don't gate on DB existence or onboarding_complete here — this page IS the
    # wizard. It creates the DB on save. Gating is in tracker.py (Prompt 4).

    step = st.session_state.get("onboarding_step", 0)

    steps = {
        0: _render_welcome,
        1: _render_questionnaire,
        2: _render_cv,
        3: _render_generate,
        4: _render_review,
        5: _render_save,
    }

    renderer = steps.get(step, _render_welcome)
    renderer()


from tracker_views.shared import is_active_page
if is_active_page(__file__):
    render()
