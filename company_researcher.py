"""
company_researcher.py — Auto-discover ATS platform and careers page for a company.

Given a company name and optional website, discovers:
  1. Whether a careers page exists
  2. Which ATS platform is used
  3. The exact board URL / slug to monitor
  4. A recommended scraping_method

Callable from CLI, Claude Code, and eventually Streamlit (spec 005).
"""

import argparse
import re
import sys
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from paths import DB_PATH
from storage import JobStorage

# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 10

RE_CAREERS_LINK = re.compile(
    r'href=["\']([^"\']*(?:careers?|jobs?|work-with-us|join|hiring|open-positions|openings)[^"\']*)["\']',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# ATS detection signatures (Phase 1 — heuristic)
# ---------------------------------------------------------------------------

# (provider, url_regex, slug_extraction_regex)
ATS_SIGNATURES: list[tuple[str, str, str]] = [
    ("greenhouse",      r"boards\.greenhouse\.io/([^/\"'&?#]+)",        r"boards\.greenhouse\.io/([^/\"'&?#]+)"),
    ("lever",           r"jobs\.lever\.co/([^/\"'&?#]+)",              r"jobs\.lever\.co/([^/\"'&?#]+)"),
    ("workable",        r"apply\.workable\.com/([^/\"'&?#]+)",         r"apply\.workable\.com/([^/\"'&?#]+)"),
    ("ashby",           r"jobs\.ashbyhq\.com/([^/\"'&?#]+)",           r"jobs\.ashbyhq\.com/([^/\"'&?#]+)"),
    ("teamtailor",      r"([^./]+)\.teamtailor\.com",                  r"([^./]+)\.teamtailor\.com"),
    ("recruitee",       r"([^./]+)\.recruitee\.com",                   r"([^./]+)\.recruitee\.com"),
    ("join.com",        r"join\.com/companies/([^/\"'&?#]+)",          r"join\.com/companies/([^/\"'&?#]+)"),
    ("bamboohr",        r"([^./]+)\.bamboohr\.com/careers",            r"([^./]+)\.bamboohr\.com"),
    ("smartrecruiters", r"careers\.smartrecruiters\.com/([^/\"'&?#]+)", r"careers\.smartrecruiters\.com/([^/\"'&?#]+)"),
    ("myworkdayjobs",   r"([^./]+)\.myworkdayjobs\.com",               r"([^./]+)\.myworkdayjobs\.com"),
]

# ---------------------------------------------------------------------------
# ResearchResult dataclass
# ---------------------------------------------------------------------------

@dataclass
class ResearchResult:
    company_name: str
    careers_url: str | None = None
    ats_provider: str | None = None
    ats_board_slug: str | None = None
    ats_board_url: str | None = None
    scraping_method: str = "none"
    detection_method: str = "heuristic"
    notes: str | None = None
    confidence: str = "low"  # "high" | "medium" | "low"


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
    def handle_data(self, data):
        self.text.append(data)
    def get_text(self):
        return " ".join(self.text)


def _extract_text(html: str, limit: int = 500) -> str:
    p = _TextExtractor()
    try:
        p.feed(html[:10000])
    except Exception:
        pass
    text = p.get_text()
    return text[:limit]


# ---------------------------------------------------------------------------
# HTTP fetching
# ---------------------------------------------------------------------------

def _fetch_page(url: str) -> tuple[str | None, str | None]:
    """Fetch a URL. Returns (html, final_url) or (None, None) on failure."""
    try:
        with httpx.Client(
            headers={"User-Agent": BROWSER_UA},
            timeout=TIMEOUT, follow_redirects=True,
        ) as client:
            r = client.get(url)
            if r.status_code == 403:
                # Try curl_cffi impersonation
                try:
                    from curl_cffi import requests as curl_requests
                    r2 = curl_requests.get(
                        url, headers={"User-Agent": BROWSER_UA},
                        timeout=TIMEOUT, impersonate="chrome124",
                    )
                    return r2.text, str(r2.url)
                except ImportError:
                    pass
                return None, None
            if r.status_code != 200:
                return None, None
            return r.text, str(r.url)
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# JS-rendered homepage fallback (Fix 4)
# ---------------------------------------------------------------------------

CAREERS_URL_GUESSES = [
    "{base}/careers",
    "{base}/jobs",
    "{base}/en/careers",
    "{base}/en/jobs",
    "{base}/about/careers",
    "{base}/company/careers",
    "{base}/work-with-us",
    "{base}/join-us",
]


def _try_careers_url_guesses(base_url: str) -> tuple[str | None, str | None]:
    """Try predictable careers URL patterns. Returns (html, final_url) of first hit."""
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    for pattern in CAREERS_URL_GUESSES:
        candidate = pattern.format(base=base)
        html, final_url = _fetch_page(candidate)
        if html and len(html) > 2000:
            return html, final_url
    return None, None


# ---------------------------------------------------------------------------
# Careers link discovery (Fix 2 — scan all, prioritise ATS)
# ---------------------------------------------------------------------------

def _find_careers_link(html: str, base_url: str) -> str | None:
    """Scan HTML for careers/jobs links. Collects all, prioritises ATS signatures."""
    candidates = []
    for m in RE_CAREERS_LINK.finditer(html):
        href = m.group(1)
        if href.startswith("#") or href.startswith("javascript:"):
            continue
        full = urljoin(base_url, href)
        # Priority: return immediately if URL already contains ATS signature
        for _, url_regex, _ in ATS_SIGNATURES:
            if re.search(url_regex, full, re.IGNORECASE):
                return full
        candidates.append(full)
    return candidates[0] if candidates else None


def _guess_homepage(name: str) -> str:
    """Guess the company homepage from name."""
    slug = name.lower().replace(" ", "").replace(".", "").replace("'", "")
    return f"https://{slug}.com"


# ---------------------------------------------------------------------------
# ATS detection (Fix 3 — scan iframes, scripts, data-* attrs)
# ---------------------------------------------------------------------------

def _detect_ats(html: str, url: str) -> tuple[str | None, str | None, str | None]:
    """Scan HTML + URL for ATS signatures. Also scans iframe srcs, script content,
    and data-* attributes for embedded job widgets."""
    combined = html + " " + url

    # Extract iframe srcs
    iframe_srcs = re.findall(
        r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    # Extract script tag content (first 2000 chars per tag, max 10 scripts)
    script_contents = re.findall(
        r'<script[^>]*>(.*?)</script>', html, re.IGNORECASE | re.DOTALL)
    # Extract data-* attributes that commonly hold ATS URLs
    data_attrs = re.findall(
        r'data-(?:greenhouse|lever|workable|ashby|careers|jobs)[^=]*=["\']([^"\']+)["\']',
        html, re.IGNORECASE)

    extra = " ".join(iframe_srcs) + " "
    extra += " ".join(c[:2000] for c in script_contents[:10]) + " "
    extra += " ".join(data_attrs)

    combined = combined + " " + extra

    for provider, _, slug_regex in ATS_SIGNATURES:
        m = re.search(slug_regex, combined, re.IGNORECASE)
        if m:
            slug = m.group(1)
            board_url = m.group(0)
            if not board_url.startswith("http"):
                board_url = f"https://{board_url}"
            return provider, slug, board_url
    return None, None, None


# ---------------------------------------------------------------------------
# LLM fallback (Phase 2)
# ---------------------------------------------------------------------------

def _classify_with_llm(name: str, url: str, html_snippet: str) -> dict | None:
    """Use LLM cascade to classify an ambiguous careers page."""
    from scorer import _call_groq_fallback_chain, _call_deepseek

    prompt = f"""Given this careers page, determine the ATS platform and how to scrape it.

Company: {name}
Careers URL: {url}
HTML snippet: {html_snippet[:500]}

Return ONLY JSON:
{{
  "ats_provider": "<name or null>",
  "ats_board_slug": "<slug or null>",
  "ats_board_url": "<full URL or null>",
  "scraping_method": "<greenhouse|lever|workable|ashby|jobspy|custom_html|manual|none>",
  "notes": "<one sentence>",
  "confidence": "<high|medium|low>"
}}"""

    messages = [
        {"role": "system", "content": "You classify company careers pages. Return ONLY JSON."},
        {"role": "user", "content": prompt},
    ]

    # Cascade: Groq 8b → Groq 70b → DeepSeek
    try:
        raw, model = _call_groq_fallback_chain(
            messages,
            models=["llama-3.1-8b-instant", "llama-3.3-70b-versatile"],
            json_mode=True, max_tokens=500,
        )
        import json as _json
        return _json.loads(raw)
    except Exception:
        pass

    try:
        raw = _call_deepseek(messages)
        import json as _json
        return _json.loads(raw)
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Main research function
# ---------------------------------------------------------------------------

def research_company(name: str, url: str | None = None,
                     existing: dict | None = None) -> ResearchResult:
    """Research a single company. No DB side effects.

    Args:
        name: Company name.
        url: Website URL (guessed if omitted).
        existing: DB company dict (from get_watch_pending_companies()).
                  If ats_provider + ats_board_slug are already set, skip all
                  fetching and return a result built from existing data.
    """
    # Fix 1: Trust existing DB data
    if existing and existing.get("ats_provider") and existing.get("ats_board_slug"):
        return ResearchResult(
            company_name=name,
            careers_url=existing.get("careers_url"),
            ats_provider=existing["ats_provider"],
            ats_board_slug=existing["ats_board_slug"],
            ats_board_url=None,
            scraping_method=existing["ats_provider"],
            detection_method="existing_data",
            notes="ATS already configured in DB — skipped fetch",
            confidence="high",
        )

    if not url:
        url = _guess_homepage(name)

    result = ResearchResult(company_name=name)

    # Step 1-2: Fetch homepage, find careers link
    html, final_url = _fetch_page(url)
    if html is None or (html and len(html) < 5000):
        # Fix 4: JS-rendered or unreachable homepage

        # First, try the existing careers_url from DB if present (Fix 5)
        if existing and existing.get("careers_url"):
            existing_careers = existing["careers_url"]
            result.careers_url = existing_careers
            careers_html, careers_final = _fetch_page(existing_careers)
            if careers_html:
                result.careers_url = careers_final or existing_careers
                provider, slug, board_url = _detect_ats(careers_html, result.careers_url)
                if provider:
                    result.ats_provider = provider
                    result.ats_board_slug = slug
                    result.ats_board_url = board_url
                    result.scraping_method = provider
                    result.detection_method = "heuristic"
                    result.confidence = "high"
                    result.notes = f"Detected {provider} board from known careers URL"
                    return result

        if html is None:
            result.notes = f"Homepage unreachable: {url}"
        else:
            result.notes = f"Homepage JS-rendered (too small): {url}"

        guess_html, guess_url = _try_careers_url_guesses(url)
        if guess_html:
            result.careers_url = guess_url
            provider, slug, board_url = _detect_ats(guess_html, guess_url or "")
            if provider:
                result.ats_provider = provider
                result.ats_board_slug = slug
                result.ats_board_url = board_url
                result.scraping_method = provider
                result.detection_method = "heuristic"
                result.confidence = "high"
                result.notes = f"Detected {provider} board via URL guess ({guess_url})"
                return result
            # Guessed URL has content but no ATS detected — try LLM
            snippet = _extract_text(guess_html, 500)
            llm_result = _classify_with_llm(name, guess_url or url, snippet)
            if llm_result:
                result.ats_provider = llm_result.get("ats_provider")
                result.ats_board_slug = llm_result.get("ats_board_slug")
                result.ats_board_url = llm_result.get("ats_board_url")
                result.scraping_method = llm_result.get("scraping_method", "manual")
                result.detection_method = "llm"
                result.notes = llm_result.get("notes", "")
                result.confidence = llm_result.get("confidence", "low")
                return result
        if html is None:
            result.scraping_method = "none"
            result.confidence = "high"
            result.detection_method = "heuristic"
            return result
        # JS-rendered with failed guesses — fall through to normal flow
        # (use the small HTML we have, try to find careers links anyway)

    # If homepage was a failure (None after all attempts)
    if html is None:
        result.scraping_method = "none"
        result.confidence = "high"
        result.notes = result.notes or f"Homepage unreachable: {url}"
        result.detection_method = "heuristic"
        return result

    careers_url = _find_careers_link(html, final_url or url)

    # Step 3: Fetch careers page if found
    careers_html = None
    if careers_url:
        result.careers_url = careers_url
        careers_html, careers_final = _fetch_page(careers_url)
        if careers_final:
            result.careers_url = careers_final

    # Step 4: Scan for ATS signatures
    if careers_html:
        provider, slug, board_url = _detect_ats(careers_html, result.careers_url or "")
        if provider:
            result.ats_provider = provider
            result.ats_board_slug = slug
            result.ats_board_url = board_url
            result.scraping_method = provider
            result.detection_method = "heuristic"
            result.confidence = "high"
            result.notes = f"Detected {provider} board from careers page"
            return result

    # Also scan homepage for ATS (some sites embed the board directly)
    provider, slug, board_url = _detect_ats(html, final_url or url)
    if provider and provider != "myworkdayjobs":
        result.ats_provider = provider
        result.ats_board_slug = slug
        result.ats_board_url = board_url
        result.scraping_method = provider
        result.detection_method = "heuristic"
        result.confidence = "high"
        result.notes = f"Detected {provider} board from homepage"
        return result

    # Step 5: No ATS match — try LLM if careers page exists
    if careers_html:
        snippet = _extract_text(careers_html, 500)
        llm_result = _classify_with_llm(name, result.careers_url or url, snippet)
        if llm_result:
            result.ats_provider = llm_result.get("ats_provider")
            result.ats_board_slug = llm_result.get("ats_board_slug")
            result.ats_board_url = llm_result.get("ats_board_url")
            result.scraping_method = llm_result.get("scraping_method", "manual")
            result.detection_method = "llm"
            result.notes = llm_result.get("notes", "")
            result.confidence = llm_result.get("confidence", "low")
            return result

        # LLM unavailable
        result.scraping_method = "custom_html"
        result.detection_method = "heuristic"
        result.confidence = "low"
        result.notes = "Careers page found but no known ATS detected"
        return result

    # Step 6: No careers page found
    if careers_url is None:
        result.scraping_method = "none"
        result.detection_method = "heuristic"
        result.confidence = "high"
        result.notes = "No careers page link found on homepage"
        return result

    result.scraping_method = "custom_html"
    result.detection_method = "heuristic"
    result.confidence = "medium"
    result.notes = "Careers page exists but no ATS signature detected"
    return result


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def update_company_from_research(db: JobStorage, company_id: int,
                                  result: ResearchResult) -> None:
    """Write research result to DB and transition monitoring status."""
    db.update_company_research(company_id, result)


def research_all_pending(db: JobStorage, save: bool = False) -> list[ResearchResult]:
    """Research all companies with monitoring_status='watch_pending'."""
    companies = db.get_watch_pending_companies()
    results: list[ResearchResult] = []
    for company in companies:
        name = company["name"]
        url = company.get("website") or company.get("careers_url")
        print(f"  Researching: {name}...")
        result = research_company(name, url, existing=company)
        results.append(result)
        if save:
            update_company_from_research(db, company["id"], result)
            print(f"    → {result.scraping_method} ({result.confidence})")
        time.sleep(1)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_table(results: list[ResearchResult]) -> None:
    header = f"{'Company':<25} {'ATS':<16} {'Slug':<20} {'Method':<14} {'Confidence':<10}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(f"{r.company_name:<25} {r.ats_provider or '—':<16} "
              f"{r.ats_board_slug or '—':<20} {r.scraping_method:<14} {r.confidence:<10}")


def main():
    parser = argparse.ArgumentParser(
        description="company_researcher — auto-discover ATS platform for companies")
    parser.add_argument("company", nargs="?", default=None,
                        help="Company name to research")
    parser.add_argument("--url", default=None,
                        help="Company website URL (optional; guessed if omitted)")
    parser.add_argument("--all", action="store_true",
                        help="Research all companies with monitoring_status='watch_pending'")
    parser.add_argument("--save", action="store_true",
                        help="Write results to DB (dry-run without this flag)")
    args = parser.parse_args()

    if args.all:
        db = JobStorage(DB_PATH)
        pending = db.get_watch_pending_companies()
        if not pending:
            print("No companies with monitoring_status='watch_pending'.")
            return
        print(f"Researching {len(pending)} watch_pending companies...\n")
        results = research_all_pending(db, save=args.save)
        _print_table(results)
        if args.save:
            print(f"\nUpdated {len([r for r in results if r.scraping_method != 'none'])} "
                  f"of {len(results)} companies in DB.")
    elif args.company:
        result = research_company(args.company, args.url)
        if args.save:
            db = JobStorage(DB_PATH)
            # Find company by name or create
            row = db.get_companies(search=args.company, exclude_blacklisted=False)
            if row and len(row) == 1:
                update_company_from_research(db, row[0]["id"], result)
                print(f"Updated {args.company} in DB.")
            else:
                print(f"Company '{args.company}' not found in DB (--save requires existing company).")
        _print_table([result])
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
