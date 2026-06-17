# Spec 004a — Company Researcher Improvements

## Context

After testing spec 004 on the Tier A watchlist, four failure modes were identified:

1. **DB data ignored** — companies already configured with `ats_provider` +
   `ats_board_slug` in the DB (e.g. 21Shares via Greenhouse/amun) are re-researched
   from scratch, fail due to JS-rendered homepages, and overwrite valid data with
   `method=none`. The researcher should trust existing DB data first.

2. **First careers link wins** — `_find_careers_link()` returns on the first regex
   match regardless of quality. If the first match is a blog post or anchor link,
   the actual careers page is never fetched.

3. **Iframes and inline scripts not scanned** — many companies embed their ATS board
   via an `<iframe src="boards.greenhouse.io/...">` or a `<script>` tag on their
   careers page. `_detect_ats()` only scans visible HTML text, missing these.

4. **JS-rendered homepages return empty HTML** — sites built on React/Next.js return
   a near-empty HTML shell (< 5KB). No careers link is ever found, and the researcher
   concludes "no careers page" even when one exists at a predictable URL.

---

## Fix 1 — Trust existing DB data (highest priority)

At the start of `research_company()`, when called from `research_all_pending()`,
check if the company already has `ats_provider` AND `ats_board_slug` set in the DB.
If both are present, skip all fetching and return a `ResearchResult` built from DB
data with `detection_method = "existing_data"` and `confidence = "high"`.

This requires passing the DB company dict (or at least `ats_provider` and
`ats_board_slug`) into `research_company()` as an optional parameter:

```python
def research_company(
    name: str,
    url: str | None = None,
    existing: dict | None = None,   # ← new optional param
) -> ResearchResult:
    # If existing DB data is complete, return immediately
    if existing and existing.get("ats_provider") and existing.get("ats_board_slug"):
        return ResearchResult(
            company_name=name,
            careers_url=existing.get("careers_url"),
            ats_provider=existing["ats_provider"],
            ats_board_slug=existing["ats_board_slug"],
            ats_board_url=existing.get("ats_board_url"),
            scraping_method=existing["ats_provider"],
            detection_method="existing_data",
            notes="ATS already configured in DB — skipped fetch",
            confidence="high",
        )
    # ... rest of algorithm
```

Update `research_all_pending()` to pass the company dict as `existing=company`.

Update `update_company_from_research()` in `storage.py` to NOT overwrite
`ats_provider`/`ats_board_slug` when `detection_method = "existing_data"` —
or simply skip the DB write entirely for these companies (just log them).

---

## Fix 2 — Scan all careers links, prioritise ATS signatures

Replace the current `_find_careers_link()` which stops at the first match with a
version that:

1. Collects **all** href matches for the careers regex
2. For each candidate, checks if the URL itself already contains an ATS signature
3. Returns the ATS-containing URL immediately if found
4. Otherwise returns the first non-anchor, non-same-page candidate

```python
def _find_careers_link(html: str, base_url: str) -> str | None:
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
```

---

## Fix 3 — Scan iframe src and script tag content for ATS signatures

Extend `_detect_ats()` to also scan:
- `<iframe src="...">` attributes
- `<script>` tag content (first 2000 chars per script tag to avoid scanning huge bundles)
- `data-greenhouse-url`, `data-lever-url` and similar `data-*` attributes common
  in embedded job widgets

```python
def _detect_ats(html: str, url: str) -> tuple[str | None, str | None, str | None]:
    # Existing: scan combined html + url text
    combined = html + " " + url

    # New: also extract iframe srcs and script content explicitly
    iframe_srcs = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    script_contents = re.findall(r'<script[^>]*>(.*?)</script>', html, re.IGNORECASE | re.DOTALL)
    extra = " ".join(iframe_srcs) + " " + " ".join(c[:2000] for c in script_contents)

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
```

---

## Fix 4 — Fallback URL guessing for JS-rendered homepages

After a failed homepage fetch (html is None) OR when the homepage HTML is too small
to contain meaningful content (< 5000 chars), attempt a set of predictable careers
URL patterns before giving up:

```python
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
```

Integrate into `research_company()`:

```python
# After homepage fetch
if html is None or len(html) < 5000:
    # JS-rendered or unreachable — try guessed careers URLs directly
    careers_html, careers_final = _try_careers_url_guesses(url)
    if careers_html:
        result.careers_url = careers_final
        provider, slug, board_url = _detect_ats(careers_html, careers_final or "")
        if provider:
            # ... return result
        # else: fall through to LLM
    else:
        result.notes = f"Homepage unreachable or JS-rendered: {url}"
        result.scraping_method = "none"
        result.confidence = "high"
        return result
```

---

## Summary of changes to `company_researcher.py`

| Function | Change |
|---|---|
| `research_company()` | Add `existing: dict \| None` param; check DB data first |
| `_find_careers_link()` | Collect all candidates; prioritise ATS-containing URLs |
| `_detect_ats()` | Also scan iframe srcs + script content + data-* attributes |
| `_try_careers_url_guesses()` | New function — predictable careers URL fallback |
| `research_all_pending()` | Pass `existing=company` to `research_company()` |

## Changes to `storage.py`

`update_company_research()` — add guard: if `detection_method == "existing_data"`,
skip DB write (or only update `researched_at`, never overwrite ATS fields).

---

## Files to modify

- **`company_researcher.py`** — all four fixes above
- **`storage.py`** — `update_company_research()` guard for `existing_data`

## Non-goals

- No Playwright/Selenium for fully JS-rendered sites (out of scope)
- No change to LLM cascade or models
- No change to CLI interface
- No change to Streamlit UI
- Do not modify any scraper or `main.py`
