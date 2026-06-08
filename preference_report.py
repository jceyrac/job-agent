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

from paths import DB_PATH
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


# ── Few-shot anchor export ──────────────────────────────────────────────────────

def export_few_shot_anchors(profile_id: str | None = None) -> str:
    """Select strongest applied/rejected jobs and clearest rejects, compress
    to one-line signatures, stratify positives across dimensions, and emit a
    Markdown block paste-ready for scoring_context.

    Returns the anchor block as a string. Also writes a companion eval-set
    file so the same jobs aren't used as both anchors and eval cases.
    """
    import random
    from profiles import get_active_profile

    pid = profile_id or get_active_profile().id
    conn = _connect()

    # ── Positives: applied, rejected, ready, queued (strong interest) ────────
    pos_rows = conn.execute(
        """SELECT j.title, j.company, COALESCE(c.industry_sector, 'other') AS sector,
                  COALESCE(j.work_mode, 'unknown') AS work_mode,
                  COALESCE(c.company_country, 'unknown') AS country,
                  s.score, s.reason AS score_reason
             FROM jobs j
             JOIN job_tracking t ON j.id = t.job_id
             JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?
             LEFT JOIN companies c ON j.company_id = c.id
            WHERE t.status IN ('applied', 'rejected', 'ready', 'queued')
              AND s.score IS NOT NULL
            ORDER BY s.score DESC""",
        (pid,),
    ).fetchall()

    # ── Negatives: archived with clear rejection pattern ─────────────────────
    neg_rows = conn.execute(
        """SELECT j.title, j.company, COALESCE(c.industry_sector, 'other') AS sector,
                  COALESCE(j.work_mode, 'unknown') AS work_mode,
                  COALESCE(c.company_country, 'unknown') AS country,
                  s.score, s.reason AS score_reason, t.notes
             FROM jobs j
             JOIN job_tracking t ON j.id = t.job_id
             JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?
             LEFT JOIN companies c ON j.company_id = c.id
            WHERE t.status = 'archived'
              AND s.score IS NOT NULL
            ORDER BY s.score ASC""",
        (pid,),
    ).fetchall()
    conn.close()

    def _sig(row) -> str:
        """One-line compressed signature."""
        title = (row["title"] or "")[:60]
        company = (row["company"] or "")[:30]
        sector = (row["sector"] or "other")
        wm = row["work_mode"] or "unknown"
        country = row["country"] or "unknown"
        score = row["score"]
        why = ""
        if row["score_reason"]:
            r = row["score_reason"].replace("\n", " ")[:80]
            why = f" — {r}"
        return f"- [{score}/10] {title} · {company} · {sector} · {wm} · {country}{why}"

    # ── Stratify positives across sector × work_mode × country ──────────────
    pos = [dict(r) for r in pos_rows]
    neg = [dict(r) for r in neg_rows]

    # Sort into buckets then take top from each bucket (breadth-first)
    buckets: dict[str, list[dict]] = {}
    for p in pos:
        key = f"{p['sector']}|{p['work_mode']}|{p['country']}"
        buckets.setdefault(key, []).append(p)

    stratified = []
    while buckets:
        for key in list(buckets.keys()):
            if buckets[key]:
                stratified.append(buckets[key].pop(0))
            else:
                del buckets[key]

    # ── Split: ~70% anchors, ~30% eval ──────────────────────────────────────
    random.seed(42)
    random.shuffle(stratified)
    random.shuffle(neg)
    split_pos = max(1, int(len(stratified) * 0.7))
    pos_anchors = sorted(stratified[:split_pos], key=lambda x: -x["score"])
    pos_eval = sorted(stratified[split_pos:], key=lambda x: -x["score"])
    split_neg = max(1, int(len(neg) * 0.7))
    neg_anchors = sorted(neg[:split_neg], key=lambda x: x["score"])
    neg_eval = sorted(neg[split_neg:], key=lambda x: x["score"])

    lines: list[str] = []
    lines.append("# Few-Shot Anchors for scoring_context")
    lines.append("")
    lines.append(f"Profile: `{pid}` | Generated: {date.today().isoformat()}")
    lines.append(f"Anchors: {len(pos_anchors)} pos + {len(neg_anchors)} neg")
    lines.append(f"Eval set: {len(pos_eval)} pos + {len(neg_eval)} neg (hold-out, do not paste into scoring_context)")
    lines.append("")

    if pos_anchors:
        lines.append("## Strong Matches (paste into scoring_context)")
        lines.append("")
        lines.append("```")
        lines.append("# Examples of jobs the candidate pursued (strong matches)")
        for r in pos_anchors:
            lines.append(_sig(r))
        lines.append("```")
        lines.append("")

    if neg_anchors:
        lines.append("## Clear Rejects (paste into scoring_context)")
        lines.append("")
        lines.append("```")
        lines.append("# Examples of jobs the candidate passed on (clear mismatches)")
        for r in neg_anchors:
            lines.append(_sig(r))
        lines.append("```")
        lines.append("")

    if pos_eval or neg_eval:
        lines.append("## Eval Hold-Out (do NOT paste)")
        lines.append("")
        lines.append("```")
        for r in pos_eval:
            lines.append(_sig(r))
        for r in neg_eval:
            lines.append(_sig(r))
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


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
    # Also generate action items alongside the report
    _act_path = generate_action_items(
        profile_id=profiles[0] if len(profiles) == 1 else None,
        output_dir=os.path.join(os.path.dirname(out_dir), "action_items") if out_dir else None,
    )
    if _act_path:
        print(f"Action items written to {_act_path}")

    return output_path


# ── Action Items generator ────────────────────────────────────────────────────────

def generate_action_items(
    profile_id: str | None = None,
    output_dir: str | None = None,
    anchor_path: str | None = None,
) -> str | None:
    """Classify calibration findings into prompt fixes vs code fixes.

    Reads the same signals as the preference report and outputs a structured
    Markdown file with a ready-to-paste Claude Code brief for code fixes.

    Returns the output file path, or None if no profile_id is given and the
    default can't be resolved.
    """
    from profiles import get_active_profile

    pid = profile_id or get_active_profile().id

    out_dir = output_dir or "outputs/action_items"
    today = date.today().isoformat()
    output_path = os.path.join(out_dir, f"action_items_{today}.md")

    conn = _connect()

    # ── Calibration data ────────────────────────────────────────────────────
    # Over-scored: archived with score >= 8, not Tier-0 cleanup
    over_rows = conn.execute(
        """SELECT j.title, j.company, s.score, t.notes, s.profile_id
             FROM jobs j
             JOIN job_tracking t ON j.id = t.job_id
             JOIN job_scores s   ON j.id = s.job_id
            WHERE t.status = 'archived' AND s.score >= 8
              AND s.profile_id = ?
              AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)
            ORDER BY s.score DESC""",
        (pid,),
    ).fetchall()

    # Under-scored: applied or rejected with score <= 6
    under_rows = conn.execute(
        """SELECT j.title, j.company, s.score, t.status, s.profile_id
             FROM jobs j
             JOIN job_tracking t ON j.id = t.job_id
             JOIN job_scores s   ON j.id = s.job_id
            WHERE t.status IN ('applied', 'rejected') AND s.score <= 6
              AND s.profile_id = ?
            ORDER BY s.score ASC""",
        (pid,),
    ).fetchall()

    tp, us, tn, fc = _get_classified_jobs(conn, pid)

    # ── Denylist candidates: companies with ≥3 archived, 0 applied/rejected ──
    deny_rows = conn.execute(
        """SELECT j.company, COUNT(*) AS n
             FROM jobs j
             JOIN job_tracking t ON j.id = t.job_id
            WHERE t.status = 'archived'
              AND j.company_id IS NOT NULL
              AND j.company NOT IN (
                  SELECT DISTINCT j2.company FROM jobs j2
                  JOIN job_tracking t2 ON j2.id = t2.job_id
                  WHERE t2.status IN ('applied', 'rejected')
              )
            GROUP BY j.company
            HAVING COUNT(*) >= 3
            ORDER BY n DESC""",
    ).fetchall()

    # ── Sector exclusions: sectors with ≥5 archived, 0 applied/rejected ─────
    sector_rows = conn.execute(
        """SELECT COALESCE(c.industry_sector, 'other') AS sector, COUNT(*) AS n
             FROM jobs j
             JOIN job_tracking t ON j.id = t.job_id
             LEFT JOIN companies c ON j.company_id = c.id
            WHERE t.status = 'archived'
              AND COALESCE(c.industry_sector, 'other') NOT IN (
                  SELECT DISTINCT COALESCE(c2.industry_sector, 'other')
                  FROM jobs j2
                  JOIN job_tracking t2 ON j2.id = t2.job_id
                  LEFT JOIN companies c2 ON j2.company_id = c2.id
                  WHERE t2.status IN ('applied', 'rejected')
              )
            GROUP BY 1
            HAVING COUNT(*) >= 5
            ORDER BY n DESC""",
    ).fetchall()

    # ── Banned country candidates: ≥3 archived, 0 applied/rejected ─────────
    country_rows = conn.execute(
        """SELECT COALESCE(j.company_country, 'unknown') AS country, COUNT(*) AS n
             FROM jobs j
             JOIN job_tracking t ON j.id = t.job_id
            WHERE t.status = 'archived'
              AND j.company_country IS NOT NULL
              AND j.company_country != 'unknown'
              AND j.company_country NOT IN (
                  SELECT DISTINCT j2.company_country FROM jobs j2
                  JOIN job_tracking t2 ON j2.id = t2.job_id
                  WHERE t2.status IN ('applied', 'rejected')
                    AND j2.company_country IS NOT NULL
              )
            GROUP BY 1
            HAVING COUNT(*) >= 3
            ORDER BY n DESC""",
    ).fetchall()

    # ── Source quality: sources with 0 strong matches in scored jobs ────────
    source_rows = conn.execute(
        """SELECT j.source, COUNT(*) AS total,
                  COUNT(CASE WHEN s.score >= 8 THEN 1 END) AS strong
             FROM jobs j
             LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?
            WHERE j.source IS NOT NULL AND j.source != ''
            GROUP BY j.source
            HAVING total >= 5 AND strong = 0
            ORDER BY total DESC""",
        (pid,),
    ).fetchall()

    # ── Few-shot anchor staleness ───────────────────────────────────────────
    anchor_stale = False
    anchor_date = ""
    if anchor_path and os.path.exists(anchor_path):
        anchor_mtime = os.path.getmtime(anchor_path)
        anchor_age_days = (date.today() - date.fromtimestamp(anchor_mtime)).days
        anchor_stale = anchor_age_days > 14
        anchor_date = date.fromtimestamp(anchor_mtime).isoformat()
    else:
        # Check outputs/preference_reports/ for anchor files
        import glob as _glob
        anchor_files = sorted(_glob.glob("outputs/preference_reports/few_shot_anchors_*.md"), reverse=True)
        if anchor_files:
            anchor_mtime = os.path.getmtime(anchor_files[0])
            anchor_age_days = (date.today() - date.fromtimestamp(anchor_mtime)).days
            anchor_stale = anchor_age_days > 14
            anchor_date = date.fromtimestamp(anchor_mtime).isoformat()

    conn.close()

    # ── Classify into prompt fixes and code fixes ───────────────────────────
    prompt_items: list[str] = []
    code_items: list[str] = []
    code_brief_lines: list[str] = []

    # P1: Scoring band miscalibration
    if len(over_rows) > 0 or len(under_rows) > 0:
        p1_lines = ["### [P1] Scoring band miscalibration", ""]
        if over_rows:
            p1_lines.append(f"**{len(over_rows)} over-scored jobs** (archived despite score ≥ 8):")
            p1_lines.append("")
            for r in over_rows[:5]:
                note = (r["notes"] or "")[:60]
                p1_lines.append(f"- [{r['score']}/10] {r['title']} · {r['company']}" +
                               (f" — {note}" if note else ""))
            p1_lines.append("")
        if under_rows:
            p1_lines.append(f"**{len(under_rows)} under-scored jobs** (applied/rejected despite score ≤ 6):")
            p1_lines.append("")
            for r in under_rows[:5]:
                p1_lines.append(f"- [{r['score']}/10] {r['title']} · {r['company']} ({r['status']})")
            p1_lines.append("")
        p1_lines.extend([
            "→ Run: `python preference_report.py --suggest-context --profile {0}`".format(pid),
            "→ Review proposal, then: `python preference_report.py --apply-context <path>`",
            "→ Verify: `python score.py --mock --profile {0}`".format(pid),
            "",
        ])
        prompt_items.append("\n".join(p1_lines))

    # P2: Few-shot anchors stale
    if anchor_stale:
        prompt_items.append(f"""### [P2] Few-shot anchors stale (last: {anchor_date})
→ Run: `python preference_report.py --anchors --profile {pid}`
→ Paste updated anchors into scoring_context, then --apply-context
""")

    # C1: Denylist candidates
    if deny_rows:
        c1_lines = [
            "### [C1] Denylist candidates",
            "",
            "Add to `denylisted_companies` in `profiles.py`:",
            "",
        ]
        from profiles import ALL_PROFILES as _CURRENT
        current = _CURRENT.get(pid)
        already_denied = set(current.denylisted_companies) if current else set()
        new_deny = [(co, n) for co, n in deny_rows if co not in already_denied]
        if new_deny:
            for co, n in new_deny:
                c1_lines.append(f"- **{co}** ({n} archived)")
            code_items.append("\n".join(c1_lines))
            code_brief_lines.append("**profiles.py — denylisted_companies** (add):")
            for co, n in new_deny:
                code_brief_lines.append(f'- "{co}"  # {n} archived')
            code_brief_lines.append("")
        else:
            c1_lines.append("_All candidates already in profile._")
            code_items.append("\n".join(c1_lines))

    # C2: Sector exclusions
    if sector_rows:
        c2_lines = [
            "### [C2] Sector exclusions",
            "",
            "Add to `excluded_sectors` in `profiles.py`:",
            "",
        ]
        from profiles import ALL_PROFILES as _C2
        current2 = _C2.get(pid)
        already_excluded = set(current2.excluded_sectors) if current2 else set()
        new_sectors = [(s, n) for s, n in sector_rows
                       if s not in already_excluded and s != "other"]
        if new_sectors:
            for s, n in new_sectors:
                c2_lines.append(f"- `{s}` ({n} archived, 0 applied)")
            code_items.append("\n".join(c2_lines))
            code_brief_lines.append("**profiles.py — excluded_sectors** (add):")
            for s, n in new_sectors:
                code_brief_lines.append(f'- "{s}"  # {n} archived')
            code_brief_lines.append("")
        else:
            c2_lines.append("_All candidates already in profile._")
            code_items.append("\n".join(c2_lines))

    # C3: Banned country candidates
    if country_rows:
        c3_lines = [
            "### [C3] Banned country candidates",
            "",
            "Add to `banned_countries` in `profiles.py`:",
            "",
        ]
        current3 = _CURRENT.get(pid) if '_CURRENT' in dir() else None
        already_banned = set(current3.banned_countries) if current3 else set()
        new_countries = [(c, n) for c, n in country_rows if c not in already_banned]
        if new_countries:
            for c, n in new_countries:
                c3_lines.append(f"- **{c}** ({n} archived, 0 applied)")
            code_items.append("\n".join(c3_lines))
            code_brief_lines.append("**profiles.py — banned_countries** (add):")
            for c, n in new_countries:
                code_brief_lines.append(f'- "{c}"  # {n} archived')
            code_brief_lines.append("")

    # C4: Source quality
    if source_rows:
        c4_lines = [
            "### [C4] Source quality",
            "",
            "These scrapers returned 0 strong matches (score ≥ 8) in scored jobs. Consider disabling:",
            "",
        ]
        for r in source_rows:
            c4_lines.append(f"- **{r['source']}**: {r['total']} scraped, 0 strong matches")
        code_items.append("\n".join(c4_lines))
        code_brief_lines.append("**Scrapers — review/disable** (0 strong matches):")
        for r in source_rows:
            code_brief_lines.append(f"- {r['source']}: {r['total']} jobs, 0 strong matches → review ENABLED flag")
        code_brief_lines.append("")

    # ── Assemble output ─────────────────────────────────────────────────────
    lines = [
        f"# Action Items — {today}",
        f"Profile: {pid} | Generated from: preference_report_{today}.md",
        "",
        "---",
        "",
        "## Prompt fixes (apply on server, no deploy needed)",
        "",
    ]

    if prompt_items:
        for item in prompt_items:
            lines.append(item)
    else:
        lines.append("_No prompt fixes found — scoring_context appears well-calibrated._")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Code fixes (implement in dev → git push → deploy)")
    lines.append("")

    if code_items:
        for item in code_items:
            lines.append(item)
            lines.append("")
    else:
        lines.append("_No code fixes identified._")
        lines.append("")

    # ── Claude Code brief ───────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## Claude Code brief")
    lines.append("")
    lines.append("<!-- Paste this block into a Claude Code session on dev to implement all code fixes -->")
    lines.append("")
    lines.append("```")
    brief_intro = [
        f"Read prompts/BUILD_feedback_loop.md for context.",
        "",
        f"Implement the following changes from the latest action items report",
        f"(outputs/action_items/action_items_{today}.md on the server):",
        "",
    ]
    if code_brief_lines:
        brief_lines = brief_intro + code_brief_lines + [
            "After implementing, run: python score.py --mock --profile unified_jc",
            "Expected: all 6 cases in their bands.",
        ]
    else:
        brief_lines = brief_intro + [
            "_No code changes needed from this report._",
        ]
    lines.extend(brief_lines)
    lines.append("```")
    lines.append("")
    lines.append("<!-- End of Claude Code brief -->")

    report = "\n".join(lines)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write(report)

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Generate a preference alignment report + action items from apply/archive data."
    )
    parser.add_argument("--profile", default=None,
                        help="Analyse a single profile (optional; defaults to the active profile).")
    parser.add_argument("--output", default=None,
                        help="Override output path for the preference report.")
    parser.add_argument("--anchors", dest="anchors", action="store_true",
                        help="Export few-shot anchors for scoring_context tuning.")
    parser.add_argument("--print", dest="print_out", action="store_true",
                        help="Also print the report to stdout.")
    parser.add_argument("--full", dest="full", action="store_true",
                        help="Generate both preference report and action items (default when no other action specified).")
    parser.add_argument("--action-items", dest="action_items", action="store_true",
                        help="Generate action items file standalone (no full report).")
    parser.add_argument("--suggest-context", dest="suggest_context", action="store_true",
                        help="Generate a scoring_context proposal via LLM. Use --dry-run to print instead of writing.")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="With --suggest-context: print proposal to stdout instead of writing to disk.")
    parser.add_argument("--apply-context", dest="apply_context", default=None,
                        metavar="PATH",
                        help="Apply a context proposal file to the DB. Does NOT call the LLM.")
    args = parser.parse_args()

    from profiles import get_active_profile
    profile_id = args.profile or get_active_profile().id

    # ── --apply-context <path> ────────────────────────────────────────────
    if args.apply_context:
        _apply_context(profile_id, args.apply_context)
        return

    # ── --suggest-context ─────────────────────────────────────────────────
    if args.suggest_context:
        from storage import JobStorage
        from context_tuner import propose_context_update
        db = JobStorage(DB_PATH)
        proposal_path = propose_context_update(
            profile_id, db, dry_run=args.dry_run,
        )
        if not args.dry_run:
            print(f"Proposal written to {proposal_path}")
            print(f"Review, then apply: python preference_report.py --apply-context {proposal_path}")
        return

    # ── --action-items (standalone) ────────────────────────────────────────
    if args.action_items:
        act_path = generate_action_items(profile_id=profile_id)
        print(f"Action items written to {act_path}")
        return

    # ── --anchors ─────────────────────────────────────────────────────────
    if args.anchors:
        anchor_block = export_few_shot_anchors(profile_id)
        out_dir = args.output and os.path.dirname(args.output) or OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)
        anchor_path = os.path.join(
            out_dir, f"few_shot_anchors_{date.today().isoformat()}.md"
        )
        with open(anchor_path, "w") as f:
            f.write(anchor_block)
        print(f"Anchors written to {anchor_path}")
        if args.print_out:
            print()
            print(anchor_block)
        return

    # ── Default / --full: generate report + action items ──────────────────
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


# ── --apply-context implementation ────────────────────────────────────────────────

def _apply_context(profile_id: str, proposal_path: str):
    """Extract proposed scoring_context from a proposal Markdown file and write it to the DB."""
    from profiles import SearchProfile
    from storage import JobStorage

    if not os.path.exists(proposal_path):
        print(f"Error: file not found: {proposal_path}")
        return

    with open(proposal_path) as f:
        content = f.read()

    # Extract block between "## Proposed scoring_context" and the next "---"
    match = re.search(r'## Proposed scoring_context\n\n(.*?)\n---', content, re.DOTALL)
    if not match:
        print("Error: could not find '## Proposed scoring_context' section in proposal file.")
        return

    new_context = match.group(1).strip()

    db = JobStorage(DB_PATH)
    row = db.get_profile(profile_id)
    if not row:
        print(f"Error: profile '{profile_id}' not found in DB.")
        return

    profile = SearchProfile.from_criteria(row["id"], row["name"], row["criteria"])
    profile.scoring_context = new_context
    db.upsert_profile(profile)

    print(f"✅ scoring_context updated for '{profile_id}'.")
    print(f"   Run: python score.py --mock --profile {profile_id}")


if __name__ == "__main__":
    main()
