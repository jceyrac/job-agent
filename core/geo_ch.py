"""geo_ch.py — deterministic "is this job in Switzerland?" classifier (spec 038).

The CV header (address, phone) is **control-flow data**, so it must be derived
deterministically from the job's country (Constitution §IV) — never proposed by
the LLM. This module is pure: no LLM, no network, no DB. It reads only *location
statements* (the posting's ``location`` field plus location-labelled lines such as
``Location:`` / ``Standort:`` / ``Based in`` / ``Remote``) — never the company
description — and classifies them into ``CH`` / ``non_CH`` / ``mixed`` / ``unknown``.

``compute_contact_profile`` is the single source of truth for the precedence:

    --contact  →  --country  →  jobs.country_code (id entries only)  →  detector  →  french

Only the cv_agent imports this module (import-boundary guard, spec 036 US6); it is
a shared domain function, not catalogue data (Constitution §X).
"""

import re

# ── Swiss signals (curated, generic, versioned in code — no personal data) ──
_SWISS_COUNTRIES = (
    "switzerland", "schweiz", "suisse", "svizzera", "svizra", "swiss",
)
_SWISS_PLACES = (
    # cities
    "zurich", "zürich", "geneva", "genève", "geneve", "lausanne", "pully",
    "bern", "berne", "basel", "bâle", "basle", "bale", "zug", "winterthur",
    "lucerne", "luzern", "lugano", "st. gallen", "st gallen", "sankt gallen",
    "fribourg", "neuchâtel", "neuchatel", "montreux", "vevey", "sion", "thun",
    "chur", "schaffhausen", "biel", "bienne", "aarau",
    # cantons
    "vaud", "ticino", "valais", "wallis", "graubünden", "graubunden", "aargau",
    "argovie", "thurgau", "schwyz", "glarus", "solothurn", "appenzell",
    "obwalden", "nidwalden", "grisons",
)
_CH_POSTAL_RE = re.compile(r"\bch[-\s]?\d{4}\b", re.IGNORECASE)
_ISO_CH_RE = re.compile(r"\bch\b", re.IGNORECASE)  # the ISO code, lowercase in prose

# ── Non-Swiss signals ───────────────────────────────────────────────────────
_NON_CH_COUNTRIES = (
    "germany", "deutschland", "allemagne", "france", "frankreich",
    "united kingdom", "great britain", "royaume-uni", "royaume uni",
    "großbritannien", "grossbritannien", "united states", "america", "usa",
    "états-unis", "etats-unis", "netherlands", "pays-bas", "spain", "espagne",
    "españa", "espana", "italy", "italia", "italie", "belgium", "belgique",
    "belgië", "belgie", "austria", "österreich", "osterreich", "autriche",
    "portugal", "ireland", "irlande", "poland", "pologne", "polen", "denmark",
    "danemark", "dänemark", "sweden", "suède", "suede", "schweden", "norway",
    "norvège", "norvege", "finland", "finlande", "finnland", "czech republic",
    "czechia", "tchéquie", "tchequie", "tschechien", "hungary", "hongrie",
    "ungarn", "romania", "roumanie", "rumania", "greece", "grèce", "grece",
    "griechenland", "luxembourg", "malta", "malte", "estonia", "estonie",
    "latvia", "lettonie", "lithuania", "lituanie", "slovakia", "slovaquie",
    "slovenia", "slovénie", "slovenie", "croatia", "croatie", "kroatien",
    "bulgaria", "bulgarie", "cyprus", "chypre", "zypern",
)
_NON_CH_PLACES = (
    "berlin", "cologne", "köln", "koln", "paris", "london", "new york",
    "amsterdam", "munich", "münchen", "munchen", "hamburg", "frankfurt",
    "vienna", "wien", "madrid", "barcelona", "milan", "milano", "rome", "roma",
    "brussels", "bruxelles", "brüssel", "brussel", "dublin", "lisbon", "lisboa",
    "warsaw", "warszawa", "prague", "praha", "budapest", "copenhagen",
    "københavn", "stockholm", "helsinki", "oslo", "cambridge", "oxford",
    "manchester", "edinburgh", "boston", "san francisco", "chicago", "toronto",
    "singapore", "dubai", "tel aviv", "bangalore", "bengaluru",
)
# Uppercase ISO-2 codes matched case-sensitively in the original text, so "us"
# (the pronoun) / "it" (information technology) never false-positive.
_ISO_NON_CH_RE = re.compile(r"\b(?:DE|UK|US|GB)\b")

# ── country → ISO 3166-1 alpha-2 (for --country, and the DB country_code) ────
_COUNTRY_TO_ISO2 = {
    "ch": "CH", "switzerland": "CH", "schweiz": "CH", "suisse": "CH",
    "svizzera": "CH", "svizra": "CH", "swiss": "CH",
    "de": "DE", "germany": "DE", "deutschland": "DE", "allemagne": "DE",
    "fr": "FR", "france": "FR", "frankreich": "FR",
    "gb": "GB", "uk": "GB", "united kingdom": "GB", "great britain": "GB",
    "royaume-uni": "GB", "royaume uni": "GB", "grossbritannien": "GB",
    "großbritannien": "GB",
    "us": "US", "usa": "US", "united states": "US", "états-unis": "US",
    "etats-unis": "US", "america": "US",
    "nl": "NL", "netherlands": "NL", "pays-bas": "NL",
    "es": "ES", "spain": "ES", "espagne": "ES", "españa": "ES", "espana": "ES",
    "it": "IT", "italy": "IT", "italia": "IT", "italie": "IT",
    "be": "BE", "belgium": "BE", "belgique": "BE",
    "at": "AT", "austria": "AT", "österreich": "AT", "osterreich": "AT",
    "autriche": "AT",
    "pt": "PT", "portugal": "PT",
    "ie": "IE", "ireland": "IE", "irlande": "IE",
    "pl": "PL", "poland": "PL", "pologne": "PL", "polen": "PL",
    "se": "SE", "sweden": "SE", "suède": "SE", "suede": "SE", "schweden": "SE",
    "dk": "DK", "denmark": "DK", "danemark": "DK", "dänemark": "DK",
    "fi": "FI", "finland": "FI", "finlande": "FI", "finnland": "FI",
    "no": "NO", "norway": "NO", "norvège": "NO", "norvege": "NO",
    "lu": "LU", "luxembourg": "LU",
}

# ── location-statement extraction (never the company description) ───────────
_LABEL_LINE_RE = re.compile(
    r"(?im)^[ \t]*(?:location|standort|lieu|arbeitsort|ort|work[ \t]+location|place[ \t]+of[ \t]+work)"
    r"[ \t]*[:：\-–][ \t]*(.+?)[ \t]*$"
)
_BASE_IN_RE = re.compile(
    r"(?i)\b(?:based|located|residing|living|positioned|situated)\s+(?:in|at|from)\s+([^,.;|\n]{1,80})"
)
_REMOTE_RE = re.compile(r"(?i)\bremote\b[^\n]{0,80}")


def _word_in(text: str, token: str) -> bool:
    """True if ``token`` appears as a whole word (Unicode-aware boundaries)."""
    return re.search(rf"\b{re.escape(token)}\b", text) is not None


def _swiss_signal(lowered: str) -> bool:
    if _CH_POSTAL_RE.search(lowered):
        return True
    if _ISO_CH_RE.search(lowered):
        return True
    if any(_word_in(lowered, name) for name in _SWISS_COUNTRIES):
        return True
    return any(_word_in(lowered, p) for p in _SWISS_PLACES)


def _non_ch_signal(text: str, lowered: str) -> bool:
    if _ISO_NON_CH_RE.search(text):  # uppercase codes, original casing
        return True
    if any(_word_in(lowered, name) for name in _NON_CH_COUNTRIES):
        return True
    return any(_word_in(lowered, p) for p in _NON_CH_PLACES)


def _extract_location_statements(text: str) -> list[str]:
    """Return the location-labelled statements in ``text`` (location lines,
    "based in …" phrases, "Remote …" phrases) — never the company blurb."""
    if not text:
        return []
    out = [m.group(1).strip() for m in _LABEL_LINE_RE.finditer(text)]
    out += [m.group(1).strip() for m in _BASE_IN_RE.finditer(text)]
    out += [m.group(0).strip() for m in _REMOTE_RE.finditer(text)]
    return [s for s in out if s]


def _location_text(job: dict) -> str:
    """Assemble the location text from a job dict: its ``location`` field plus
    the location statements found in its ``description``."""
    job = job or {}
    parts = []
    loc = (job.get("location") or "").strip()
    if loc:
        parts.append(loc)
    parts.extend(_extract_location_statements(job.get("description") or ""))
    return "\n".join(parts)


# ── public API ──────────────────────────────────────────────────────────────

def classify_location(text: str) -> str:
    """Classify location text as ``"CH"`` | ``"non_CH"`` | ``"mixed"`` | ``"unknown"``.

    ``CH`` = ≥1 Swiss signal and 0 non-Swiss; ``non_CH`` = 0 Swiss and ≥1 non-Swiss;
    ``mixed`` = ≥1 of each; ``unknown`` = neither (or no location statement).
    Pure and deterministic — the same input always yields the same verdict.
    """
    if not text or not text.strip():
        return "unknown"
    lowered = text.lower()
    swiss = _swiss_signal(lowered)
    non_ch = _non_ch_signal(text, lowered)
    if swiss and non_ch:
        return "mixed"
    if swiss:
        return "CH"
    if non_ch:
        return "non_CH"
    return "unknown"


def normalize_country(raw: str) -> str | None:
    """Map a ``--country`` value (ISO-2 code or a common name) to an ISO-2 code,
    or ``None`` when unrecognised. "Suisse" → ``CH``, "Germany" → ``DE``, "ch" → ``CH``."""
    if not raw:
        return None
    key = raw.strip().lower()
    if not key:
        return None
    if re.fullmatch(r"[a-z]{2}", key):
        return key.upper()
    return _COUNTRY_TO_ISO2.get(key)


def compute_contact_profile(
    entry_kind: str, job: dict,
    contact_override: str | None, country_override: str | None,
) -> tuple[str, str, str | None]:
    """Decide the contact profile deterministically: ``(profile, source, flag)``.

    Precedence (single source of truth — see research.md §3):

    1. ``--contact`` ∈ {swiss, french}  → that value, source ``explicit``
    2. ``--country``                     → swiss iff ISO-2 == CH, source ``explicit_country``
    3. ``jobs.country_code`` (id only)   → swiss iff CH, source ``db_country``
    4–7. location detector               → CH/non_CH/mixed/unknown (sources ``detected``/``default``)

    ``flag`` is set only for a mixed/unknown country (then profile == ``french``).
    Missing data always resolves to ``french`` — never raises.
    """
    if contact_override in ("swiss", "french"):
        return (contact_override, "explicit", None)

    if country_override:
        cc = normalize_country(country_override)
        if cc:
            return ("swiss" if cc == "CH" else "french", "explicit_country", None)
        # unrecognised --country value → fall through to the detector

    job = job or {}
    if entry_kind == "id":
        cc = normalize_country(job.get("country_code") or "")
        if cc:
            return ("swiss" if cc == "CH" else "french", "db_country", None)

    verdict = classify_location(_location_text(job))
    if verdict == "CH":
        return ("swiss", "detected", None)
    if verdict == "non_CH":
        return ("french", "detected", None)
    if verdict == "mixed":
        return ("french", "detected", "mixed_locations")
    return ("french", "default", "unknown_country")
