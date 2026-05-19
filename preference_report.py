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

ALL_PROFILES = ["web3_remote", "ch_hybrid", "unified_jc"]


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


def _get_scores_for_profile(conn, profile_id: str):
    rows = conn.execute(
        """SELECT j.id, t.status, s.score, s.reason
             FROM jobs j
             JOIN job_tracking t ON j.id = t.job_id
             JOIN job_scores s   ON j.id = s.job_id
            WHERE s.profile_id = ?
              AND t.status IN ('applied', 'rejected', 'archived')""",
        (profile_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_applications_count(conn):
    return conn.execute("SELECT COUNT(*) FROM job_applications").fetchone()[0]


# ── Tokenisation ────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-zàâçéèêëîïôûùüÿœæ]{3,}", (text or "").lower())
    return [t for t in tokens if t not in STOPWORDS]


# ── Report sections ─────────────────────────────────────────────────────────────

def _section_header(conn, profiles: list[str]) -> list[str]:
    rel = _get_cohort_jobs(conn, ["applied", "rejected"])
    nrel = _get_cohort_jobs(conn, ["archived"])
    total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    scored = conn.execute("SELECT COUNT(DISTINCT job_id) FROM job_scores").fetchone()[0]
    prepared = _get_applications_count(conn)

    lines = [
        f"# Preference Report — {date.today().isoformat()}",
        "",
        "| Metric | Count |",
        "|---|---|",
        f"| Relevant (applied + rejected) | {len(rel)} |",
        f"| Not relevant (archived) | {len(nrel)} |",
        f"| Total jobs in DB | {total_jobs} |",
        f"| Jobs with at least one score | {scored} |",
        f"| Prepared applications | {prepared} |",
        f"| Profiles analysed | {', '.join(profiles)} |",
        "",
    ]
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
        scores = _get_scores_for_profile(conn, pid)
        if not scores:
            lines.append(f"### {pid} — no data")
            lines.append("")
            continue

        relevant = [s for s in scores if s["status"] in ("applied", "rejected")]
        nrel = [s for s in scores if s["status"] == "archived"]
        if not relevant and not nrel:
            lines.append(f"### {pid} — no cohort data")
            lines.append("")
            continue

        lines.append(f"### {pid}")
        lines.append(f"_relevant={len(relevant)}, not_relevant={len(nrel)}_")
        lines.append("")
        lines.append("| Score bucket | Relevant | Not relevant | Calibration |")
        lines.append("|---|---|---|---|")

        for label, fn in buckets:
            r_count = sum(1 for s in relevant if fn(s["score"]))
            nr_count = sum(1 for s in nrel if fn(s["score"]))
            cal = ""
            if "0-4" in label or "5-6" in label:
                if r_count > 0 and r_count >= nr_count:
                    cal = "👀 under-scored"
            if "7-8" in label or "9-10" in label:
                if nr_count > r_count:
                    cal = "🚨 over-scored"
            lines.append(f"| {label} | {r_count} | {nr_count} | {cal} |")

        lines.append("")
    return lines


def _section_categorical(conn) -> list[str]:
    lines = ["## Categorical Breakdowns", ""]
    rel = _get_cohort_jobs(conn, ["applied", "rejected"])
    nrel = _get_cohort_jobs(conn, ["archived"])
    dimensions = [
        "industry_sector",
        "work_mode",
        "geo_zone",
        "company_country",
        "language_required",
        "company_size",
    ]

    for dim in dimensions:
        lines.append(f"### {dim}")
        counter_rel = Counter(j[dim] for j in rel)
        counter_nrel = Counter(j[dim] for j in nrel)
        all_vals = set(counter_rel.keys()) | set(counter_nrel.keys())
        rankings = sorted(all_vals, key=lambda v: counter_rel[v] + counter_nrel[v],
                          reverse=True)[:10]

        lines.append("| Value | Relevant | Not relevant | Relevant share |")
        lines.append("|---|---|---|---|")

        for val in rankings:
            r = counter_rel.get(val, 0)
            nr = counter_nrel.get(val, 0)
            total = r + nr
            share = f"{r / total * 100:.0f}%" if total > 0 else "—"
            lines.append(f"| {val} | {r} | {nr} | {share} |")
        lines.append("")

        # Flags
        flags = []
        for val in rankings:
            r = counter_rel.get(val, 0)
            nr = counter_nrel.get(val, 0)
            if nr >= 5 and r == 0:
                flags.append(f"  - **{val}**: {nr} not_relevant / {r} relevant — strong negative signal")
            if r >= 5 and nr == 0:
                flags.append(f"  - **{val}**: {r} relevant / {nr} not_relevant — strong positive signal")
        if flags:
            lines.append("")
            for f in flags:
                lines.append(f)
            lines.append("")
    return lines


def _section_company_hotspots(conn) -> list[str]:
    lines = ["## Company Hotspots", ""]
    rel = _get_cohort_jobs(conn, ["applied", "rejected"])
    nrel = _get_cohort_jobs(conn, ["archived"])

    rel_by_co = Counter(j["company"] for j in rel)
    nrel_by_co = Counter(j["company"] for j in nrel)

    # Allowlist candidates: ≥ 2 relevant jobs
    allow = [(co, cnt, nrel_by_co.get(co, 0))
             for co, cnt in rel_by_co.items() if cnt >= 2]
    allow.sort(key=lambda x: -x[1])

    lines.append(f"### Allowlist Candidates ({len(allow)} companies with ≥ 2 relevant)")
    lines.append("")
    lines.append("| Company | Relevant | Not relevant |")
    lines.append("|---|---|---|")
    for co, r, nr in allow:
        lines.append(f"| {co} | {r} | {nr} |")
    lines.append("")

    # Denylist candidates: ≥ 3 not-relevant, 0 relevant
    deny = [(co, cnt, rel_by_co.get(co, 0))
            for co, cnt in nrel_by_co.items() if cnt >= 3 and rel_by_co.get(co, 0) == 0]
    deny.sort(key=lambda x: -x[1])

    lines.append(f"### Denylist Candidates ({len(deny)} companies with ≥ 3 not_relevant, 0 relevant)")
    lines.append("")
    lines.append("| Company | Not relevant | Relevant |")
    lines.append("|---|---|---|")
    for co, nr, r in deny:
        lines.append(f"| {co} | {nr} | {r} |")
    lines.append("")

    # Ambiguous: appear in both with ≥ 2 total
    ambiguous = [(co, rel_by_co.get(co, 0), nrel_by_co.get(co, 0))
                 for co in set(rel_by_co) & set(nrel_by_co)
                 if rel_by_co[co] + nrel_by_co[co] >= 2]
    ambiguous.sort(key=lambda x: -(x[1] + x[2]))
    if ambiguous:
        lines.append(f"### Ambiguous ({len(ambiguous)} companies in both cohorts)")
        lines.append("")
        lines.append("| Company | Relevant | Not relevant |")
        lines.append("|---|---|---|")
        for co, r, nr in ambiguous:
            lines.append(f"| {co} | {r} | {nr} |")
        lines.append("")
    return lines


def _section_calibration_deltas(conn) -> list[str]:
    lines = ["## Calibration Deltas", ""]

    # Over-scored: score ≥ 8 but archived
    over_rows = conn.execute(
        """SELECT j.title, j.company, s.score, s.profile_id, t.notes
             FROM jobs j
             JOIN job_tracking t ON j.id = t.job_id
             JOIN job_scores s   ON j.id = s.job_id
            WHERE t.status = 'archived' AND s.score >= 8
            ORDER BY s.score DESC
            LIMIT 10"""
    ).fetchall()

    lines.append("### Over-scored (LLM score ≥ 8 but archived)")
    lines.append("")
    if over_rows:
        lines.append("| Score | Profile | Title | Company | Archive note |")
        lines.append("|---|---|---|---|---|")
        for r in over_rows:
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


def _section_suggestions(conn) -> list[str]:
    lines = ["## Suggested Edits to profiles.py", ""]
    rel = _get_cohort_jobs(conn, ["applied", "rejected"])
    nrel = _get_cohort_jobs(conn, ["archived"])

    sector_rel = Counter(j["industry_sector"] for j in rel)
    sector_nrel = Counter(j["industry_sector"] for j in nrel)
    for sector in sorted(sector_nrel.keys()):
        if sector_nrel[sector] >= 5 and sector_rel.get(sector, 0) == 0:
            lines.append(
                f"- **Add `{sector}` to `excluded_sectors`**: "
                f"{sector_nrel[sector]} archives, 0 applies — strong negative signal"
            )

    co_rel = Counter(j["company"] for j in rel)
    co_nrel = Counter(j["company"] for j in nrel)
    deny_cos = [(co, cnt) for co, cnt in co_nrel.items()
                if cnt >= 3 and co_rel.get(co, 0) == 0]
    if deny_cos:
        names = ", ".join(f"**{co}**" for co, _ in sorted(deny_cos, key=lambda x: -x[1])[:6])
        lines.append(f"- **Add to `denylisted_companies`**: {names}")

    geo_rel = Counter(j["geo_zone"] for j in rel)
    geo_nrel = Counter(j["geo_zone"] for j in nrel)
    if geo_nrel.get("global_remote", 0) > geo_rel.get("global_remote", 0) * 2:
        lines.append(
            f"- **Consider tightening `global_remote` scoring**: "
            f"{geo_nrel['global_remote']} archives vs {geo_rel.get('global_remote', 0)} applies"
        )

    wm_rel = Counter(j["work_mode"] for j in rel)
    wm_nrel = Counter(j["work_mode"] for j in nrel)
    if wm_nrel.get("on_site", 0) > wm_rel.get("on_site", 0) * 3:
        lines.append(
            f"- **On-site bias**: {wm_nrel['on_site']} on-site jobs archived "
            f"vs {wm_rel.get('on_site', 0)} applied — on-site penalty in scoring works"
        )

    co_country_rel = Counter(j["company_country"] for j in rel)
    co_country_nrel = Counter(j["company_country"] for j in nrel)
    for country in sorted(co_country_nrel.keys()):
        if country == "unknown":
            continue
        if co_country_nrel[country] >= 3 and co_country_rel.get(country, 0) == 0:
            lines.append(
                f"- **Consider flagging `{country}` in scoring**: "
                f"{co_country_nrel[country]} archives, 0 applies"
            )

    if len(lines) == 2:
        lines.append("_No strong suggestions based on current data._")
    lines.append("")
    return lines


# ── CLI ─────────────────────────────────────────────────────────────────────────

def generate_report(profile_id: str | None = None,
                    output_dir: str | None = None) -> str:
    """Generate the report and return the output file path."""
    profiles = [profile_id] if profile_id else ALL_PROFILES
    conn = _connect()

    sections = [
        _section_header(conn, profiles),
        _section_score_distribution(conn, profiles),
        _section_categorical(conn),
        _section_company_hotspots(conn),
        _section_calibration_deltas(conn),
        _section_archive_themes(conn),
        _section_title_keywords(conn),
        _section_suggestions(conn),
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
                        help="Analyse a single profile (e.g. unified_jc).")
    parser.add_argument("--output", default=None,
                        help="Override output path.")
    parser.add_argument("--print", dest="print_out", action="store_true",
                        help="Also print the report to stdout.")
    args = parser.parse_args()

    path = generate_report(
        profile_id=args.profile,
        output_dir=os.path.dirname(args.output) if args.output else None,
    )

    # If a specific output path was given, move the generated file there
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
