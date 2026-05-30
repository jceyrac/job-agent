#!/usr/bin/env python3
"""Generate a preference-alignment markdown report from apply/archive behaviour.

Read-only. No LLM calls. No DB writes. Stdlib only.
"""

import argparse
import os
import re
import sqlite3
from collections import Counter
from datetime import date

DB_PATH = "data/jobs.db"
OUTPUT_DIR = "outputs/preference_reports"

STOPWORDS = {
    "a", "about", "after", "again", "all", "also", "am", "an", "and", "any",
    "are", "as", "at", "be", "been", "but", "by", "can", "could",
    "did", "do", "does", "down", "each", "even", "for", "from",
    "get", "got", "had", "has", "have", "he", "her", "here", "hers", "him",
    "his", "how", "i", "if", "in", "into", "is", "it", "its", "just",
    "like", "made", "make", "me", "more", "most", "my",
    "no", "nor", "not", "now", "of", "on", "or", "other", "our", "out",
    "over", "said", "same", "she", "should", "so", "some", "such",
    "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "through", "to", "too", "under", "until", "up", "us",
    "very", "vs", "was", "we", "were", "what", "when", "where", "which",
    "who", "why", "will", "with", "would", "you", "your",
    "afin", "ainsi", "alors", "après", "au", "aucun", "aussi", "autre",
    "aux", "avec", "avoir", "car", "ce", "cela", "ces", "cet", "cette",
    "ceux", "chaque", "chez", "comme", "comment", "dans", "de", "depuis",
    "des", "donc", "dont", "du", "elle", "elles", "en", "encore", "entre",
    "est", "et", "eux", "fait", "faire", "il", "ils", "je", "la", "le",
    "les", "leur", "leurs", "lui", "mais", "même", "moi", "moins",
    "ne", "ni", "nous", "on", "ont", "ou", "où", "par", "pas", "pendant",
    "peu", "plus", "pour", "puis", "que", "quel", "quelle", "quelles",
    "quels", "qui", "sa", "sans", "se", "ses", "son", "sont", "sous",
    "sur", "toi", "toujours", "tout", "tous", "toute", "toutes", "très",
    "trop", "tu", "un", "une", "uniquement", "vers", "vos", "vous", "y",
}

def _get_default_profiles() -> list[str]:
    from profiles import get_active_profile
    return [get_active_profile().id]


# ── DB helpers ──────────────────────────────────────────────────────────────────

def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = 1")
    return conn


def _get_cohort_jobs(conn, statuses: list[str]):
    placeholders = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"""SELECT j.id, j.title, j.company, j.company_id, j.url,
                   t.status, t.notes,
                   COALESCE(c.industry_sector, 'other') AS industry_sector,
                   COALESCE(j.work_mode, 'unknown')     AS work_mode,
                   COALESCE(j.geo_zone, 'unknown')      AS geo_zone,
                   COALESCE(c.company_country, 'unknown') AS company_country,
                   COALESCE(j.language_required, 'unknown') AS language_required,
                   COALESCE(c.company_size, 'unknown')  AS company_size
              FROM jobs j
              JOIN job_tracking t ON j.id = t.job_id
              LEFT JOIN companies c ON j.company_id = c.id
             WHERE t.status IN ({placeholders})""",
        statuses,
    ).fetchall()
    return [dict(r) for r in rows]


def _get_applications_count(conn):
    return conn.execute("SELECT COUNT(*) FROM job_applications").fetchone()[0]


def _get_classified_jobs(conn, profile_id: str):
    """Return (true_positive, llm_under_scored, true_negative, filter_cleanup)
    as lists of job dicts with score info, classified per-profile."""
    rows = conn.execute(
        """SELECT j.id, j.title, j.company, j.company_id, j.url,
                  t.status, t.notes,
                  COALESCE(c.industry_sector, 'other') AS industry_sector,
                  COALESCE(j.work_mode, 'unknown')     AS work_mode,
                  COALESCE(j.geo_zone, 'unknown')      AS geo_zone,
                  COALESCE(c.company_country, 'unknown') AS company_country,
                  COALESCE(j.language_required, 'unknown') AS language_required,
                  COALESCE(c.company_size, 'unknown')  AS company_size,
                  s.score, s.reason AS score_reason, s.scored_by
             FROM jobs j
             JOIN job_tracking t ON j.id = t.job_id
             LEFT JOIN companies c ON j.company_id = c.id
             LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?
            WHERE t.status IN ('applied', 'rejected', 'archived')""",
        (profile_id,),
    ).fetchall()

    tp, us, tn, fc = [], [], [], []
    for r in rows:
        d = dict(r)
        status = d["status"]
        sb = (d.get("scored_by") or "")
        score = d.get("score")
        is_tier0_reject = sb.startswith("tier_0") and score is not None and score <= 3

        if status in ("applied", "rejected"):
            if is_tier0_reject:
                us.append(d)
            else:
                tp.append(d)
        else:  # archived
            if is_tier0_reject:
                fc.append(d)
            else:
                tn.append(d)

    assert len(tp) + len(us) + len(tn) + len(fc) == len(rows), \
        "cohort classification is not exhaustive"
    return tp, us, tn, fc


def _get_company_sector(conn, company_name: str) -> str:
    row = conn.execute(
        "SELECT industry_sector FROM companies WHERE name = ?", (company_name,)
    ).fetchone()
    return (row["industry_sector"] or "other") if row else "other"


# ── Tokenisation ────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-zàâçéèêëîïôûùüÿœæ]{3,}", (text or "").lower())
    return [t for t in tokens if t not in STOPWORDS]


# ── Report sections ─────────────────────────────────────────────────────────────

def _section_header(conn, profiles: list[str]) -> list[str]:
    total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    scored = conn.execute("SELECT COUNT(DISTINCT job_id) FROM job_scores").fetchone()[0]
    prepared = _get_applications_count(conn)

    lines = [
        f"# Preference Report — {date.today().isoformat()}",
        "",
        "| Metric | Count |",
        "|---|---|",
        f"| Total jobs in DB | {total_jobs} |",
        f"| Jobs with at least one score | {scored} |",
        f"| Prepared applications | {prepared} |",
        f"| Profiles analysed | {', '.join(profiles)} |",
        "",
    ]

    for pid in profiles:
        tp, us, tn, fc = _get_classified_jobs(conn, pid)
        relevant = len(tp) + len(us)
        lines.append(f"### {pid}")
        lines.append("")
        lines.append("| Cohort | Count |")
        lines.append("|---|---|")
        lines.append(f"| Relevant decisions | {relevant} |")
        lines.append(f"| · Genuine apply/reject | {len(tp)} |")
        if us:
            lines.append(f"| · 🚨 Applied despite Tier-0 reject | {len(us)} |")
        lines.append(f"| Negative decisions | {len(tn)} |")
        lines.append(f"| Filter cleanup (excluded from analysis) | {len(fc)} |")
        lines.append("")

    return lines


def _section_score_distribution(conn, profiles: list[str]) -> list[str]:
    lines = ["## Score Distribution by Cohort", ""]
    buckets = [
        ("9-10", lambda s: s >= 9),
        ("7-8",  lambda s: 7 <= s <= 8),
        ("5-6",  lambda s: 5 <= s <= 6),
        ("0-4",  lambda s: s <= 4),
    ]

    for pid in profiles:
        tp, us, tn, fc = _get_classified_jobs(conn, pid)
        relevant = tp + us

        scored_relevant = [j for j in relevant if j.get("score") is not None]
        scored_tn = [j for j in tn if j.get("score") is not None]

        if not scored_relevant and not scored_tn:
            lines.append(f"### {pid} — no scored cohort data")
            lines.append("")
            continue

        lines.append(f"### {pid}")
        lines.append(f"_relevant={len(scored_relevant)}, true_neg={len(scored_tn)}, "
                     f"filter_cleanup={len(fc)}_")
        lines.append("")
        lines.append("| Score bucket | Relevant | True negative | Calibration |")
        lines.append("|---|---|---|---|")

        for label, fn in buckets:
            r_count = sum(1 for j in scored_relevant if fn(j["score"]))
            tn_count = sum(1 for j in scored_tn if fn(j["score"]))
            cal = ""
            if "0-4" in label or "5-6" in label:
                if r_count > 0 and r_count >= tn_count:
                    cal = "👀 under-scored"
            if "7-8" in label or "9-10" in label:
                if tn_count > r_count:
                    cal = "🚨 over-scored"
            lines.append(f"| {label} | {r_count} | {tn_count} | {cal} |")

        lines.append("")
    return lines


def _section_categorical(conn, profiles: list[str]) -> list[str]:
    """Categorical breakdowns per profile using true_positive+llm_under as
    relevant and true_negative as not_relevant."""
    lines = ["## Categorical Breakdowns", ""]
    dimensions = [
        "industry_sector", "work_mode", "geo_zone",
        "company_country", "language_required", "company_size",
    ]

    for pid in profiles:
        tp, us, tn, fc = _get_classified_jobs(conn, pid)
        rel = tp + us

        if not rel and not tn:
            continue

        lines.append(f"### {pid}")
        lines.append(f"_relevant={len(rel)}, true_neg={len(tn)}, "
                     f"filter_cleanup={len(fc)}_")
        lines.append("")

        for dim in dimensions:
            counter_rel = Counter(j.get(dim, "unknown") for j in rel)
            counter_tn = Counter(j.get(dim, "unknown") for j in tn)
            all_vals = set(counter_rel.keys()) | set(counter_tn.keys())
            rankings = sorted(all_vals,
                              key=lambda v: counter_rel[v] + counter_tn[v],
                              reverse=True)[:10]

            lines.append(f"#### {dim}")
            lines.append("| Value | Relevant | True negative | Relevant share |")
            lines.append("|---|---|---|---|")

            for val in rankings:
                r = counter_rel.get(val, 0)
                nr = counter_tn.get(val, 0)
                total = r + nr
                share = f"{r / total * 100:.0f}%" if total > 0 else "—"
                lines.append(f"| {val} | {r} | {nr} | {share} |")
            lines.append("")

            flags = []
            for val in rankings:
                r = counter_rel.get(val, 0)
                nr = counter_tn.get(val, 0)
                if nr >= 5 and r == 0:
                    flags.append(f"  - **{val}**: {nr} true_neg / {r} relevant — strong negative signal")
                if r >= 5 and nr == 0:
                    flags.append(f"  - **{val}**: {r} relevant / {nr} true_neg — strong positive signal")
            if flags:
                for f in flags:
                    lines.append(f)
                lines.append("")
    return lines


def _section_company_hotspots(conn, profiles: list[str]) -> list[str]:
    lines = ["## Company Hotspots", ""]

    for pid in profiles:
        tp, us, tn, fc = _get_classified_jobs(conn, pid)
        rel = tp + us

        rel_by_co = Counter(j["company"] for j in rel)
        tn_by_co = Counter(j["company"] for j in tn)

        allow = [(co, cnt, tn_by_co.get(co, 0))
                 for co, cnt in rel_by_co.items() if cnt >= 2]
        allow.sort(key=lambda x: -x[1])

        deny = [(co, cnt, rel_by_co.get(co, 0))
                for co, cnt in tn_by_co.items()
                if cnt >= 3 and rel_by_co.get(co, 0) == 0]
        deny.sort(key=lambda x: -x[1])

        ambiguous = [(co, rel_by_co.get(co, 0), tn_by_co.get(co, 0))
                     for co in set(rel_by_co) & set(tn_by_co)
                     if rel_by_co[co] + tn_by_co[co] >= 2]
        ambiguous.sort(key=lambda x: -(x[1] + x[2]))

        lines.append(f"### {pid}")
        lines.append("")

        if allow:
            lines.append(f"**Allowlist Candidates** ({len(allow)} companies with ≥ 2 relevant)")
            lines.append("")
            lines.append("| Company | Relevant | True negative |")
            lines.append("|---|---|---|")
            for co, r, nr in allow:
                lines.append(f"| {co} | {r} | {nr} |")
            lines.append("")

        if deny:
            lines.append(f"**Denylist Candidates** ({len(deny)} companies with ≥ 3 true_neg, 0 relevant)")
            lines.append("")
            lines.append("| Company | True negative | Relevant |")
            lines.append("|---|---|---|")
            for co, nr, r in deny:
                lines.append(f"| {co} | {nr} | {r} |")
            lines.append("")

        if ambiguous:
            lines.append(f"**Ambiguous** ({len(ambiguous)} companies in both cohorts)")
            lines.append("")
            lines.append("| Company | Relevant | True negative |")
            lines.append("|---|---|---|")
            for co, r, nr in ambiguous:
                lines.append(f"| {co} | {r} | {nr} |")
            lines.append("")

    return lines


def _section_llm_under_scored(conn, profiles: list[str]) -> list[str]:
    """New section: jobs applied to despite Tier-0 rejection."""
    lines = ["## 🚨 LLM under-scored — applied despite Tier-0 rejection", ""]

    for pid in profiles:
        _, us, _, _ = _get_classified_jobs(conn, pid)
        if not us:
            lines.append(f"### {pid} — none")
            lines.append("")
            continue

        lines.append(f"### {pid} — {len(us)} jobs")
        lines.append("")
        lines.append("| Title | Company | Score | Tier-0 reason | Status |")
        lines.append("|---|---|---|---|---|")

        rule_counter = Counter()
        for j in sorted(us, key=lambda x: x.get("score") or 0):
            reason = (j.get("score_reason") or "")[:60].replace("|", "\\|")
            # Extract rule prefix before colon: "filtered: work_mode (on-site)" → "work_mode"
            rule = "unknown"
            if ": " in reason:
                rule = reason.split(": ")[1].split(" (")[0] if " (" in reason.split(": ")[1] else reason.split(": ")[1]
            rule_counter[rule] += 1
            lines.append(
                f"| {j['title']} | {j['company']} | {j.get('score', '?')} "
                f"| {reason} | {j['status']} |"
            )
        lines.append("")

        lines.append("**By rule:**")
        for rule, cnt in rule_counter.most_common():
            lines.append(f"  - {rule}: {cnt} jobs")
        lines.append("")

        top_rule = rule_counter.most_common(1)
        if top_rule:
            lines.append(
                f"Consider loosening the **{top_rule[0][0]}** filter — it's blocking "
                f"jobs you actually want. Review the table above and decide whether to "
                f"remove specific values from the rule's blocklist."
            )
        lines.append("")

    return lines


def _section_calibration_deltas(conn) -> list[str]:
    lines = ["## Calibration Deltas", ""]

    # Over-scored: score ≥ 8 but archived AND not a Tier-0 cleanup
    over_rows = conn.execute(
        """SELECT j.title, j.company, s.score, s.profile_id, t.notes, s.scored_by
             FROM jobs j
             JOIN job_tracking t ON j.id = t.job_id
             JOIN job_scores s   ON j.id = s.job_id
            WHERE t.status = 'archived' AND s.score >= 8
            ORDER BY s.score DESC
            LIMIT 15"""
    ).fetchall()

    # Filter out Tier-0 cleanup (the LLM didn't over-score these)
    real_over = [r for r in over_rows
                 if not ((r["scored_by"] or "").startswith("tier_0") and r["score"] <= 3)]

    lines.append("### Over-scored (LLM score ≥ 8 but archived, excluding Tier-0 cleanup)")
    lines.append("")
    if real_over:
        lines.append("| Score | Profile | Title | Company | Archive note |")
        lines.append("|---|---|---|---|---|")
        for r in real_over[:10]:
            note = (r["notes"] or "")[:80].replace("|", "\\|")
            lines.append(
                f"| {r['score']} | {r['profile_id']} | {r['title']} "
                f"| {r['company']} | {note} |"
            )
    else:
        lines.append("None — no over-scored jobs found.")
    lines.append("")

    # Under-scored: score ≤ 6 but applied or rejected
    under_rows = conn.execute(
        """SELECT j.title, j.company, s.score, s.profile_id, t.status
             FROM jobs j
             JOIN job_tracking t ON j.id = t.job_id
             JOIN job_scores s   ON j.id = s.job_id
            WHERE t.status IN ('applied', 'rejected') AND s.score <= 6
            ORDER BY s.score ASC"""
    ).fetchall()

    lines.append("### Under-scored (LLM score ≤ 6 but applied or rejected)")
    lines.append("")
    if under_rows:
        lines.append("| Score | Profile | Title | Company | Status |")
        lines.append("|---|---|---|---|---|")
        for r in under_rows:
            lines.append(
                f"| {r['score']} | {r['profile_id']} | {r['title']} "
                f"| {r['company']} | {r['status']} |"
            )
    else:
        lines.append("None — no under-scored jobs found.")
    lines.append("")
    return lines


def _section_archive_themes(conn) -> list[str]:
    lines = ["## Archive Note Themes", ""]
    rows = conn.execute(
        "SELECT notes FROM job_tracking WHERE status = 'archived' AND notes IS NOT NULL AND notes != ''"
    ).fetchall()

    word_counts = Counter()
    for r in rows:
        word_counts.update(_tokenize(r["notes"]))

    lines.append("### Top 30 words in archive notes")
    lines.append("")
    lines.append("| Word | Count |")
    lines.append("|---|---|")
    for word, count in word_counts.most_common(30):
        lines.append(f"| {word} | {count} |")
    lines.append("")
    return lines


def _section_title_keywords(conn) -> list[str]:
    lines = ["## Title Keyword Frequency Deltas", ""]
    rel = _get_cohort_jobs(conn, ["applied", "rejected"])
    nrel = _get_cohort_jobs(conn, ["archived"])

    rel_words = Counter()
    for j in rel:
        rel_words.update(_tokenize(j["title"]))
    nrel_words = Counter()
    for j in nrel:
        nrel_words.update(_tokenize(j["title"]))

    all_words = set(rel_words) | set(nrel_words)
    rel_n = len(rel) or 1
    nrel_n = len(nrel) or 1

    scores = {}
    for w in all_words:
        total = rel_words[w] + nrel_words[w]
        if total < 5:
            continue
        rel_freq = rel_words[w] / rel_n
        nr_freq = nrel_words[w] / nrel_n
        ratio = (rel_freq + 0.001) / (nr_freq + 0.001)
        scores[w] = (rel_words[w], nrel_words[w], ratio)

    positive = [(w, *vals) for w, vals in scores.items() if vals[2] > 1.5]
    negative = [(w, *vals) for w, vals in scores.items() if vals[2] < 0.67]

    lines.append(f"### Positive bias (more common in relevant, n={len(positive)})")
    lines.append("")
    if positive:
        lines.append("| Word | Relevant | Not relevant | Score |")
        lines.append("|---|---|---|---|")
        for w, rc, nrc, ratio in sorted(positive, key=lambda x: -x[3])[:20]:
            lines.append(f"| {w} | {rc} | {nrc} | {ratio:.2f} |")
    lines.append("")

    lines.append(f"### Negative bias (more common in archived, n={len(negative)})")
    lines.append("")
    if negative:
        lines.append("| Word | Relevant | Not relevant | Score |")
        lines.append("|---|---|---|---|")
        for w, rc, nrc, ratio in sorted(negative, key=lambda x: x[3])[:20]:
            lines.append(f"| {w} | {rc} | {nrc} | {ratio:.2f} |")
    lines.append("")
    return lines


def _section_suggestions(conn, profiles: list[str]) -> list[str]:
    lines = ["## Suggested Edits to profiles.py", ""]

    try:
        from profiles import ALL_PROFILES as CURRENT_PROFILES
    except ImportError:
        CURRENT_PROFILES = {}

    skipped_all = set()

    for pid in profiles:
        tp, us, tn, fc = _get_classified_jobs(conn, pid)
        rel = tp + us

        current = CURRENT_PROFILES.get(pid)
        if current is None:
            already_excluded_sectors = set()
            already_banned_countries = set()
            already_denylisted = set()
        else:
            already_excluded_sectors = set(current.excluded_sectors or [])
            already_banned_countries = set(current.banned_countries or [])
            already_denylisted = set(current.denylisted_companies or [])

        lines.append(f"### {pid}")
        lines.append("")

        # Sector suggestions
        sector_rel = Counter(j["industry_sector"] for j in rel)
        sector_tn = Counter(j["industry_sector"] for j in tn)
        for sector in sorted(sector_tn.keys()):
            if sector == "other":
                continue
            if sector_tn[sector] >= 5 and sector_rel.get(sector, 0) == 0:
                if sector in already_excluded_sectors:
                    skipped_all.add(f"sector:{sector}")
                    continue
                lines.append(
                    f"- **Add `{sector}` to `excluded_sectors`**: "
                    f"{sector_tn[sector]} true_neg, 0 applies"
                )

        # Company suggestions
        co_rel = Counter(j["company"] for j in rel)
        co_tn = Counter(j["company"] for j in tn)

        deny_direct = []
        deny_review = []
        for co, cnt in co_tn.items():
            if cnt < 3 or co_rel.get(co, 0) > 0:
                continue
            if co in already_denylisted:
                skipped_all.add(f"company:{co}")
                continue
            sector = _get_company_sector(conn, co)
            if sector in already_excluded_sectors or sector in ("other", "unknown"):
                deny_direct.append((co, cnt, sector))
            else:
                deny_review.append((co, cnt, sector))

        if deny_direct:
            names = ", ".join(f"**{co}**" for co, _, _ in sorted(deny_direct, key=lambda x: -x[1]))
            lines.append(f"- **Add to `denylisted_companies`**: {names}")
        if deny_review:
            for co, cnt, sector in sorted(deny_review, key=lambda x: -x[1]):
                lines.append(
                    f"- **Review (don't auto-denylist): {co}** ({cnt} archives) — "
                    f"`{sector}` is a target sector. Likely role-specific rather than "
                    f"company-wide. Inspect titles before adding to denylist."
                )

        # Country suggestions
        co_country_rel = Counter(j["company_country"] for j in rel)
        co_country_tn = Counter(j["company_country"] for j in tn)
        for country in sorted(co_country_tn.keys()):
            if country == "unknown":
                continue
            if co_country_tn[country] >= 3 and co_country_rel.get(country, 0) == 0:
                if country in already_banned_countries:
                    skipped_all.add(f"country:{country}")
                    continue
                lines.append(
                    f"- **Consider flagging `{country}` in scoring**: "
                    f"{co_country_tn[country]} true_neg, 0 applies"
                )

        # Geo zone
        geo_rel = Counter(j["geo_zone"] for j in rel)
        geo_tn = Counter(j["geo_zone"] for j in tn)
        if geo_tn.get("global_remote", 0) > geo_rel.get("global_remote", 0) * 2:
            lines.append(
                f"- **Consider tightening `global_remote` scoring**: "
                f"{geo_tn['global_remote']} true_neg vs "
                f"{geo_rel.get('global_remote', 0)} applies"
            )

        if len(lines) >= 2 and not lines[-1].startswith("-"):
            lines.append("_No strong suggestions for this profile._")
        lines.append("")

    if skipped_all:
        skipped_list = ", ".join(sorted(skipped_all))
        lines.append(f"*(Already in profile: {skipped_list})*")
        lines.append("")

    return lines


# ── CLI ─────────────────────────────────────────────────────────────────────────

def generate_report(profile_id: str | None = None,
                    output_dir: str | None = None) -> str:
    """Generate the report and return the output file path."""
    profiles = [profile_id] if profile_id else _get_default_profiles()
    conn = _connect()

    sections = [
        _section_header(conn, profiles),
        _section_score_distribution(conn, profiles),
        _section_categorical(conn, profiles),
        _section_company_hotspots(conn, profiles),
        _section_llm_under_scored(conn, profiles),
        _section_calibration_deltas(conn),
        _section_archive_themes(conn),
        _section_title_keywords(conn),
        _section_suggestions(conn, profiles),
    ]
    conn.close()

    report = "\n".join(line for sec in sections for line in sec)

    out_dir = output_dir or OUTPUT_DIR
    output_path = os.path.join(
        out_dir, f"preference_report_{date.today().isoformat()}.md"
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write(report)
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Generate a preference alignment report from apply/archive data."
    )
    parser.add_argument("--profile", default=None,
                        help="Analyse a single profile (optional; defaults to the active profile).")
    parser.add_argument("--output", default=None,
                        help="Override output path.")
    parser.add_argument("--print", dest="print_out", action="store_true",
                        help="Also print the report to stdout.")
    args = parser.parse_args()

    from profiles import get_active_profile
    profile_id = args.profile or get_active_profile().id

    path = generate_report(
        profile_id=profile_id,
        output_dir=os.path.dirname(args.output) if args.output else None,
    )

    if args.output and args.output != path:
        os.makedirs(os.path.dirname(args.output), exist_ok=True)
        os.replace(path, args.output)
        path = args.output

    print(f"Report written to {path}")

    if args.print_out:
        print()
        with open(path) as f:
            print(f.read())


if __name__ == "__main__":
    main()
