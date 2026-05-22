import json
import os
import re
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from models import JobPosting
from profiles import ALL_PROFILES
from scorer import extract_job_fields, evaluate_for_profile
from storage import JobStorage

DB_PATH = "data/jobs.db"


# ---------------------------------------------------------------------------
# Shared helpers (moved from score.py to break circular import)
# ---------------------------------------------------------------------------

def _dict_to_posting(d: dict) -> JobPosting:
    """Reconstruct a JobPosting from a DB row dict (for storage write calls).
    Company-level fields (country, sector, size) come from companies table joins,
    not from the jobs table — they are absent from raw jobs rows after Phase 5.
    """
    posted_date = None
    raw_date = d.get("posted_date")
    if raw_date:
        try:
            posted_date = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            pass
    extracted_at = d.get("extracted_at")
    if extracted_at:
        try:
            extracted_at = datetime.strptime(str(extracted_at)[:19], "%Y-%m-%dT%H:%M:%S")
        except (ValueError, TypeError):
            extracted_at = None
    return JobPosting(
        source=d.get("source") or "",
        title=d.get("title") or "",
        company=d.get("company") or "",
        location=d.get("location") or "",
        url=d.get("url") or "",
        posted_date=posted_date,
        description=d.get("description"),
        tags=[],
        salary=None,
        work_mode=d.get("work_mode"),
        base_location=d.get("base_location"),
        summary=d.get("summary"),
        company_size=d.get("company_size"),
        contract_type=d.get("contract_type"),
        geo_zone=d.get("geo_zone"),
        country_code=d.get("country_code"),
        company_country=d.get("company_country"),
        industry_sector=d.get("industry_sector"),
        language_required=d.get("language_required"),
        extracted_at=extracted_at,
        extracted_by=d.get("extracted_by"),
        company_summary=d.get("company_summary"),
        company_website=d.get("company_website"),
    )


def _discover_contacts(job, description: str, company_id: int | None, db) -> tuple[int, int]:
    """Extract contacts from a job description via regex + optional gated LLM.

    Returns (regex_count, llm_count).
    """
    from scrapers.contact_extract import extract_contact_references

    if company_id is None:
        return 0, 0

    regex_count = 0
    llm_count = 0
    desc = description or ""

    # ── Regex extraction (always runs) ──────────────────────────────────────
    try:
        refs = extract_contact_references(desc)
    except Exception as e:
        print(f"    ⚠️  Contact regex extraction error: {e}")
        refs = []

    regex_emails: set[str] = set()
    regex_linkedins: set[str] = set()
    for ref in refs:
        try:
            cid = db.upsert_contact(
                company_id=company_id,
                email=ref.get("email"),
                linkedin_url=ref.get("linkedin_url"),
                email_status="role_account" if ref.get("role_account") else "unknown",
                is_unverified=True,
            )
            db.log_interaction(
                company_id=company_id,
                contact_id=cid,
                job_id=job.id,
                type="discovered_on_posting",
                direction="none",
                body_excerpt=desc[:200],
            )
            regex_count += 1
            if ref.get("email"):
                regex_emails.add(ref["email"])
            if ref.get("linkedin_url"):
                regex_linkedins.add(ref["linkedin_url"])
        except Exception as e:
            print(f"    ⚠️  Contact upsert error: {e}")

    # ── Gated LLM extraction (JOB_AGENT_LLM_CONTACTS=1) ────────────────────
    if os.environ.get("JOB_AGENT_LLM_CONTACTS") != "1":
        if regex_count:
            print(f"    Contacts: {regex_count} found via regex "
                  f"({len(regex_emails)} email, {len(regex_linkedins)} linkedin)")
        print(f"    LLM contact extraction: skipped (env disabled)")
        return regex_count, 0

    # Pre-gate: skip LLM if description has no contact signals
    pre_gate = re.search(
        r"@|linkedin\.com|recruiter|hiring\s*manager|talent|please\s*contact|"
        r"apply\s*directly|for\s*questions",
        desc, re.IGNORECASE,
    )
    if not pre_gate:
        if regex_count:
            print(f"    Contacts: {regex_count} found via regex "
                  f"({len(regex_emails)} email, {len(regex_linkedins)} linkedin)")
        print(f"    LLM contact extraction: skipped (pre-gate: no contact signals)")
        return regex_count, 0

    print(f"    LLM contact extraction: enabled, running...")
    try:
        from scorer import CONTACT_EXTRACTION_PROMPT, _call_groq_fallback_chain

        prompt = (
            f"Title: {job.title}\n"
            f"Company: {job.company}\n"
            f"Description: {desc[:3000]}"
        )
        messages = [
            {"role": "system", "content": CONTACT_EXTRACTION_PROMPT},
            {"role": "user", "content": prompt},
        ]
        raw, model = _call_groq_fallback_chain(messages, max_tokens=500)
        result = json.loads(raw)
        llm_contacts = result.get("contacts", []) if isinstance(result, dict) else []
    except Exception as e:
        print(f"    ⚠️  LLM contact extraction failed: {e}")
        llm_contacts = []

    for contact in llm_contacts:
        email = (contact.get("email") or "").lower().strip() or None
        linkedin = contact.get("linkedin_url") or None
        if linkedin:
            # Normalize LinkedIn URL
            li_match = re.search(r"linkedin\.com/in/([\w\-%]+)", linkedin, re.IGNORECASE)
            if li_match:
                linkedin = f"https://www.linkedin.com/in/{li_match.group(1)}"

        # Dedupe: skip if regex already found this
        if email and email in regex_emails:
            continue
        if linkedin and linkedin in regex_linkedins:
            continue

        try:
            cid = db.upsert_contact(
                company_id=company_id,
                email=email,
                linkedin_url=linkedin,
                full_name=contact.get("name"),
                role_title=contact.get("role_title"),
                x_handle=contact.get("x_handle"),
                is_unverified=True,
            )
            db.log_interaction(
                company_id=company_id,
                contact_id=cid,
                job_id=job.id,
                type="discovered_on_posting",
                direction="none",
                body_excerpt=desc[:200],
            )
            llm_count += 1
        except Exception as e:
            print(f"    ⚠️  LLM contact upsert error: {e}")

    total = regex_count + llm_count
    parts = [f"{regex_count} via regex"]
    if llm_count:
        parts.append(f"{llm_count} via LLM")
    print(f"    Contacts: {total} found ({', '.join(parts)})")
    return regex_count, llm_count


# ---------------------------------------------------------------------------
# Single-job helpers
# ---------------------------------------------------------------------------

def extract_one(job_id: str) -> dict | None:
    """Run profile-independent field extraction for one job.

    Idempotent: if job.extracted_at is already set, skips the LLM call.
    Returns None only on unrecoverable load failure (job not found).
    """
    db = JobStorage(DB_PATH)
    row = db.get_job_for_prepare(job_id)
    if row is None:
        return None

    job = _dict_to_posting(row)
    if job.extracted_at is not None:
        return {"status": "already_extracted"}

    result = extract_job_fields(job)
    if result is None:
        return {"status": "error", "error": "extraction failed"}

    db.update_job_extraction(result.id, {
        "company_country":   result.company_country or "unknown",
        "industry_sector":   result.industry_sector or "other",
        "language_required": result.language_required or "unknown",
        "work_mode":         result.work_mode or "unknown",
        "geo_zone":          result.geo_zone or "unknown",
        "country_code":      result.country_code,
        "company_size":      result.company_size or "unknown",
        "contract_type":     result.contract_type or "unknown",
        "summary":           result.summary or "",
        "extracted_by":      result.extracted_by,
    })

    company_id = row.get("company_id")
    if company_id is not None:
        db.update_company_metadata(
            company_id,
            summary=getattr(result, "company_summary", None),
            website=getattr(result, "company_website", None),
        )

    _discover_contacts(
        result, result.description or "",
        row.get("company_id"), db,
    )

    return {
        "status": "ok",
        "extracted_by": result.extracted_by,
        "summary": result.summary,
        "geo_zone": result.geo_zone,
        "work_mode": result.work_mode,
        "company_country": result.company_country,
        "industry_sector": result.industry_sector,
        "language_required": result.language_required,
        "company_size": result.company_size,
        "contract_type": result.contract_type,
    }


def score_one(job_id: str, profile_id: str | None = None) -> dict | None:
    """Run extract-if-needed + evaluate_for_profile for one job.

    Returns None only on unrecoverable load failure (job not found).
    """
    from profiles import get_active_profile
    profile_id = profile_id or get_active_profile().id

    if profile_id not in ALL_PROFILES:
        return {"status": "error", "error": f"unknown profile {profile_id}"}

    db = JobStorage(DB_PATH)
    row = db.get_job_for_prepare(job_id)
    if row is None:
        return None

    if row.get("extracted_at") is None:
        ext = extract_one(job_id)
        if ext is None or ext.get("status") == "error":
            return ext or {"status": "error", "error": "extraction failed"}
        row = db.get_job_for_prepare(job_id)

    profile = ALL_PROFILES[profile_id]
    db.upsert_profile(profile)
    job = _dict_to_posting(row)
    result = evaluate_for_profile(job, profile)

    if result is None:
        db.save_unscored(job)
        return {"status": "error", "error": "evaluation failed"}

    db.save_scored(job, result, profile.id)
    return {
        "status": "ok",
        "score": result.get("score"),
        "reason": result.get("reason"),
        "scored_by": result.get("scored_by"),
    }


def prepare_one(job_id: str, profile_id: str | None = None,
                redo: bool = False) -> dict | None:
    """Thin wrapper around prepare.prepare_job_application."""
    from prepare import prepare_job_application
    try:
        result = prepare_job_application(
            job_id=job_id, profile_id=profile_id, redo=redo, mock=False)
        if result is None:
            return {"status": "already_prepared_or_missing"}
        return {"status": "ok", **result}
    except Exception as e:
        return {"status": "error", "error": str(e)}
