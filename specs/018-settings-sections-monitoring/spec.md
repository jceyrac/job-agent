# Spec 018 — Settings sections + monitoring master-switch

> Spec for Claude Code. Read `tracker_views/settings.py`, `tracker_views/shared.py`,
> `scrape.py`, `scrapers/base.py`, `scrapers/greenhouse.py`, `scrapers/ats/lever.py`,
> `scrapers/ats/ashby.py`, and the remaining `scrapers/ats/*.py` +
> `scrapers/company_sites/*.py` **in full** before starting.
>
> This task **explicitly concerns** `scrapers/base.py`, several files under
> `scrapers/`, and `scrape.py` — all listed as *stable core* in the constitution.
> The change is scoped to two additive class attributes on the scrapers, two
> pipeline gates, and the Settings section layout. Do **not** touch `models.py`,
> `storage.py` schema or migrations, `profiles.py`, `scorer.py`, `score.py`,
> `main.py`, `onboarding.py`, the Companies page CRUD (`companies.py`,
> `company_detail.py`, `forms.py`, `company_researcher.py`), or the profile editor
> (`_render_profile_editor`, owned by spec 017). Any scraper `fetch()` body stays
> unchanged — only class attributes are added.

---

## Context (investigation-first)

Follows spec 017 (profile field consolidation). 017 cleaned the *profile editor*;
018 cleans the rest of Settings — the scraper toggles and monitoring — into the
three-section information architecture the user asked for: **Job's profile** (done
in 017), **Broad scraping**, **Company Monitoring**.

Current `settings.py` renders a single `_render_scraper_toggles` that lists **all**
scrapers in one undifferentiated grid with a hardcoded `CRYPTO_WEB3_NAMES` set, plus
a separate `_render_company_monitoring` that only exposes a pipeline checkbox and a
count. Monitoring management proper lives on the Companies page.

### Two acquisition models, enforced today only by directory convention

Per **Constitution Principle VIII**, scrapers are either query-driven **boards** or
**company-keyed** sources. The directory layout already encodes this
(`scrapers/boards/`, `scrapers/ats/`, `scrapers/company_sites/`, plus `greenhouse.py`
at root), but `discover_scrapers()` flattens it and records nothing, so the UI cannot
tell them apart. It infers "crypto" scrapers from a hardcoded name set instead.

### The company-keyed scrapers are **not homogeneous** (confirmed by reading fetch)

| Scraper | `targets is None` (broad path) behaviour | Runs in broad? |
|---|---|---|
| `greenhouse.py` | uses `profile.greenhouse_boards` or `get_greenhouse_boards()` (seed + DB watching) | **Yes — discovery** |
| `ats/lever.py` | `get_lever_slugs()` = seed `["impossiblecloud"]` + DB watching-lever | **Yes — discovery** |
| `ats/ashby.py` | `if self._targets is None: return []` | **No — monitoring-only** |
| `ats/*` others (gem, recruitee, smartrecruiters, workable, workday) | **verify per file** — some may seed, some may early-return | per file |
| `company_sites/*` (sygnum, tangem) | dedicated single-company scrapers | per file |

So "Greenhouse runs on both sides" (the user's decision A) is not a Greenhouse
special case — it is the behaviour of every company-keyed scraper that has a
seed/discovery fallback. Ashby is the counter-example (monitoring-only). The UI must
represent this honestly, not force every ATS into one section.

### Config keys that already exist (reuse, do not reinvent)

- `scrape.enabled_in_pipeline` — whether the **broad** path runs in `main.py`.
- `monitoring.enabled_in_pipeline` — whether the **monitored** path runs in `main.py`.
- `scraper.<slug>.enabled` — per-scraper on/off, where
  `slug = SOURCE_NAME.lower().replace(" ","_").replace(":","_")` (see
  `BaseScraper.enabled_config_key()`). **Checked in `_run_broad_scrape`** via
  `scraper.is_enabled()`. **Not checked in `_run_monitored_only`** — monitoring
  currently runs every monitored company regardless of any per-provider switch.

### The single real gap

There is **no per-ATS gate for the monitoring path**. `_run_monitored_only` groups
`get_monitored_companies()` by `ats_provider` and scrapes every group. The user's
non-destructive master-switch needs a new key —
`monitoring.ats.<provider>.enabled` — checked in `_run_monitored_only`, that pauses a
provider's monitored scrape **without** touching any company's `monitored` flag.

> Note: `shared.py::is_monitoring_source_enabled()` currently reads the **broad**
> key `scraper.<provider>.enabled` to answer a monitoring question — a latent
> mismatch. Part of this spec repoints monitoring semantics to the new key (see file
> 5). Grep its usages first and repoint carefully.

---

## Goal

Reorganise Settings into the three sections, split scraper toggles by acquisition
model (derived from a class attribute, not a hardcoded name set), and add a
non-destructive per-ATS monitoring master-switch with the cascade the user
specified. After this change: boards live under **Broad scraping**; company-keyed
sources live under **Company Monitoring** grouped by provider; discovery-capable ATS
(Greenhouse, Lever, …) also expose their broad-discovery toggle under Broad scraping;
disabling an ATS master-switch pauses only that provider's monitored scrape and
preserves company state; enabling monitoring for a company under a paused ATS
re-arms the switch.

Decisions driving this spec (confirmed): **A** — keep discovery-capable ATS running
on both sides (broad discovery of unknown companies + monitored companies). **B(i)**
— the Monitoring section is read + quick toggles + an "add a monitorable company to
monitoring" dropdown; full company CRUD (adding unknown companies, ATS detection)
stays on the Companies page.

---

## Files to modify

### 1. `scrapers/base.py` — two additive class attributes

```python
class BaseScraper(ABC):
    SOURCE_NAME: str = "Unknown"
    ENABLED: bool = True
    ACQUISITION_MODEL: str = "board"        # "board" | "company_keyed"
    SUPPORTS_DISCOVERY: bool = True         # runs a seed/known-company sweep in the broad path
```

Defaults describe a query-driven board (the majority): `board`, discovery-capable.
No behaviour change — these are read by `scrape.py` and the UI only.

### 2. Company-keyed scrapers — set the attributes (one/two lines each, no `fetch` change)

Set `ACQUISITION_MODEL = "company_keyed"` on every scraper under `scrapers/ats/`,
`scrapers/company_sites/`, and `scrapers/greenhouse.py`. For each, set
`SUPPORTS_DISCOVERY` to reflect its **actual** `targets is None` behaviour verified
during reading:

- `greenhouse.py`: `company_keyed`, `SUPPORTS_DISCOVERY = True`.
- `ats/lever.py`: `company_keyed`, `SUPPORTS_DISCOVERY = True`.
- `ats/ashby.py`: `company_keyed`, `SUPPORTS_DISCOVERY = False` (early-returns without targets).
- `ats/gem.py`, `ats/recruitee.py`, `ats/smartrecruiters.py`, `ats/workable.py`,
  `ats/workday.py`: `company_keyed`; set `SUPPORTS_DISCOVERY` per file — `True` if the
  scraper has a seed/DB-watching fallback when `targets is None`, `False` if it
  early-returns `[]`. **Read each `fetch` to decide; do not guess.**
- `company_sites/sygnum.py`, `company_sites/tangem.py`: `company_keyed`. These are
  dedicated single-company scrapers; set `SUPPORTS_DISCOVERY` to match whether they
  run standalone in the broad path today (they have no `targets` list — likely
  `True`, self-contained). Verify.

Board scrapers under `scrapers/boards/` need **no change** (they inherit the
`board` / discovery-capable defaults).

### 3. `scrape.py` — two gates

**(a) `_run_broad_scrape`** — after `discover_scrapers()`, skip company-keyed
scrapers that do not support discovery, so the broad path no longer instantiates
monitoring-only ATS (currently Ashby wastes a no-op call):

```python
for ScraperClass in scraper_classes:
    if (getattr(ScraperClass, "ACQUISITION_MODEL", "board") == "company_keyed"
            and not getattr(ScraperClass, "SUPPORTS_DISCOVERY", False)):
        continue  # monitoring-only source — not part of the broad/discovery sweep
    scraper = ScraperClass(storage=db)
    if not scraper.is_enabled():
        ...
```

The existing per-scraper `is_enabled()` check (`scraper.<slug>.enabled`) stays — it
remains the broad-discovery toggle for boards and discovery-capable ATS.

**(b) `_run_monitored_only`** — honour the new per-provider monitoring gate. After
grouping `by_provider`, skip a provider whose monitoring switch is explicitly off:

```python
for provider, companies in sorted(by_provider.items()):
    gate = db.get_config(f"monitoring.ats.{provider}.enabled")
    if gate is not None and gate.lower() == "false":
        print(f"[{provider}] monitoring paused (master switch off) — {len(companies)} companies skipped")
        continue
    ...
```

Absent key → enabled (default on). Skipping is non-destructive: no write to any
company row. Do not change anything else in `_run_monitored_only`.

**Independence invariant (do NOT violate).** `_run_monitored_only` MUST read
**only** `monitoring.ats.<provider>.enabled`, and MUST NOT read
`scraper.<slug>.enabled`. Conversely `_run_broad_scrape` MUST read **only**
`scraper.<slug>.enabled` (via `is_enabled()`), and MUST NOT read the
`monitoring.ats.*` key. The two switches are deliberately orthogonal: a
discovery-capable ATS (Greenhouse, Lever, …) can be turned **off in broad while
staying on in monitoring**, and vice versa. The concrete user scenario this enables:
monitor a small hand-picked company list via Greenhouse while removing Greenhouse
from the broad discovery sweep — no confusion, no coupling. Do NOT add a convenience
"if the scraper is disabled anywhere, skip it everywhere" shortcut; that would break
this invariant.

### 4. `tracker_views/settings.py` — section reorg

Replace `_render_scraper_toggles` and `_render_company_monitoring` with two clearly
separated renderers. Keep `_render_setup`, `_render_profile_editor` (017),
`_render_purge`, `_render_reonboard`, and the `_render_run_controls` untouched.
Update `render()` ordering to: Setup → Profile Editor (017) → **Broad scraping** →
**Company Monitoring** → Purge → Re-onboard.

Discover scrapers once via `from scrape import discover_scrapers` and partition:

```python
scrapers = sorted(discover_scrapers(), key=lambda c: c.SOURCE_NAME)
boards = [c for c in scrapers if getattr(c, "ACQUISITION_MODEL", "board") == "board"]
company_keyed = [c for c in scrapers if getattr(c, "ACQUISITION_MODEL", "board") == "company_keyed"]
discovery_ats = [c for c in company_keyed if getattr(c, "SUPPORTS_DISCOVERY", False)]
```

#### 4a. `_render_broad_scraping(db)`

- `st.subheader("🕸 Broad scraping")` + one-line caption ("Query-driven boards and
  discovery sweeps of known companies. Independent of the monitoring path.").
- The `scrape.enabled_in_pipeline` checkbox (move the existing logic here verbatim,
  including the "at least one scrape source must remain active" guard that also reads
  `monitoring.enabled_in_pipeline`).
- A toggle grid (3 columns, as today) for `boards` **plus** `discovery_ats`. Each
  toggle reads/writes `ScraperCls.enabled_config_key()` (`scraper.<slug>.enabled`),
  same code as the current `_render_scraper_toggles` inner loop. Label discovery ATS
  with a suffix, e.g. `f"{name} (discovery)"`, so the user understands a Greenhouse
  toggle here governs the broad seed sweep, not monitoring. This toggle and the
  provider's monitoring master-switch (in the Company Monitoring section) are
  independent — a caption should make that explicit, e.g. "Governs broad discovery
  only; monitoring of hand-picked companies is set in Company Monitoring below."
- Remove the hardcoded `CRYPTO_WEB3_NAMES` set and the `🪙` labelling logic entirely —
  it was a stand-in for real classification.
- (The `search_locations` override input already lives in the 017 profile editor;
  do **not** duplicate it here. If the user later wants it beside the board toggles,
  that is a separate change — out of scope.)

#### 4b. `_render_company_monitoring(db)`

- `st.subheader("📡 Company Monitoring")` + caption ("Company-keyed ATS sources. Each
  provider can be paused without losing its company list.").
- The `monitoring.enabled_in_pipeline` checkbox (move existing logic here verbatim,
  including the mutual "at least one source active" guard).
- Load the monitorable set once:
  `from tracker_views.shared import load_all_monitorable_companies` →
  `all_companies = load_all_monitorable_companies(db)`. Group by `ats_provider`
  (skip rows without a provider). Only providers present among `company_keyed`
  scrapers are shown; map provider string → scraper via a case-insensitive match on
  `SOURCE_NAME` / the ats module name.
- For each provider group render an `st.expander(f"{provider} — {n_monitored} monitored / {n_monitorable} monitorable")`:
  - **Master switch (non-destructive):** a `st.checkbox("Monitor this ATS in the
    pipeline", value=<gate>)` bound to `monitoring.ats.<provider>.enabled` (absent →
    True). On change, `db.set_config("monitoring.ats.<provider>.enabled", "true"/"false")`
    and `st.rerun()`. **Never** touch company `monitored` flags here.
  - **Monitored companies list:** each monitored company of the provider with a quick
    `🔕 Unmonitor` button that calls the **existing** storage method that clears a
    company's `monitored` flag (the one `company_detail.py` already uses — grep for
    it; do not invent a new one) and reruns.
  - **Add-to-monitoring dropdown (B(i)):** a `st.selectbox` of that provider's
    monitorable-but-not-monitored companies. On selection + an "➕ Monitor" button:
    set the company's `monitored` flag via the same existing storage method, **and**
    if `monitoring.ats.<provider>.enabled` is currently `"false"`, flip it back to
    `"true"` (the cascade re-arm). Rerun.
- Below the groups, keep a one-line summary
  (`f"{len(all_companies)} monitorable companies ({active} monitored)"`), as today.
- Full company creation / ATS detection stays on the Companies page — add a
  `st.caption` linking there ("Add new companies or run ATS detection on the
  Companies page.") rather than duplicating that UI.

### 5. `tracker_views/shared.py` — monitoring-source helper

`is_monitoring_source_enabled(db, company)` currently reads the **broad** key
`scraper.<provider>.enabled`, which now has a distinct meaning (broad discovery).
Repoint it to the monitoring key:

```python
def is_monitoring_source_enabled(db, company: dict) -> bool:
    provider = (company.get("ats_provider") or company.get("scraper_id") or "").strip()
    if not provider:
        return True
    val = db.get_config(f"monitoring.ats.{provider}.enabled")
    if val is None:
        return True
    return val.lower() == "true"
```

Grep all call sites first. If any caller depends on the old broad-key meaning, keep a
separate `is_broad_source_enabled` for that and give this one the monitoring meaning.
Add the cached grouping loader only if the section code needs it; otherwise reuse
`load_all_monitorable_companies`.

---

## Non-objectives

- No change to the profile editor (`_render_profile_editor`) — spec 017 owns it.
- No change to any scraper `fetch()` body, request logic, or the seed slug lists.
  Only `ACQUISITION_MODEL` / `SUPPORTS_DISCOVERY` class attributes are added.
- No DB schema change and no migration. The cascade rides entirely on config keys
  (`monitoring.ats.<provider>.enabled`) plus the existing company `monitored` flag.
- No relocation of the Companies-page CRUD, `add_company_dialog`, ATS research
  (`company_researcher.py`), or `monitoring_status`/`watch_*` lifecycle. The
  Monitoring section only toggles the boolean `monitored` flag and the per-ATS gate.
- No Greenhouse/Lever discovery removal — decision A keeps both sides. (The seed-based
  discovery sweeps stay exactly as they run today; they are merely surfaced as Broad
  toggles.)
- No consolidation of `greenhouse_boards` into monitored companies — that data
  migration, if ever wanted, is a separate spec. `greenhouse_boards` stays a profile
  field (managed in the 017 editor).
- No change to `main.py` pipeline order.

---

## Constitution alignment

- **VIII (scrapers by acquisition model):** made explicit in code (`ACQUISITION_MODEL`)
  and in the UI split; the monitoring path gains the per-provider gate the principle
  implies, and `--monitored-only` still runs independently (now honouring the gate).
- **II (two paths):** all switches are config writes straight to the DB (prose path,
  no deploy); the pipeline gates and class attributes are the accompanying code path.
- **V (surgical):** two class attributes + two small pipeline gates + a Settings
  reorg; no schema change, no `fetch` change, no new dependency; blast radius bounded
  to the listed files, most edits one line.
- **III / IX:** unchanged — single profile, scoring stays optional; this spec only
  touches acquisition/monitoring configuration.

---

## Acceptance criteria

- [ ] `BaseScraper` exposes `ACQUISITION_MODEL` and `SUPPORTS_DISCOVERY`; every
      `scrapers/ats/*`, `scrapers/company_sites/*`, and `greenhouse.py` sets
      `ACQUISITION_MODEL = "company_keyed"` with `SUPPORTS_DISCOVERY` matching its real
      `targets is None` behaviour (Greenhouse/Lever True, Ashby False; others verified).
- [ ] `_run_broad_scrape` skips company-keyed scrapers with `SUPPORTS_DISCOVERY =
      False`; boards and discovery-capable ATS still run and still respect
      `scraper.<slug>.enabled`.
- [ ] `_run_monitored_only` skips a provider whose `monitoring.ats.<provider>.enabled`
      is `"false"` and prints a paused message; absent key → runs; **no company row is
      written when a provider is skipped**.
- [ ] Settings shows three sections in order: Profile Editor (017), **Broad scraping**,
      **Company Monitoring**. The old single scraper-toggle grid and the hardcoded
      `CRYPTO_WEB3_NAMES` / `🪙` logic are gone.
- [ ] Broad scraping lists board scrapers + discovery ATS (Greenhouse/Lever shown with
      a "discovery" label); toggling one writes `scraper.<slug>.enabled`.
- [ ] Company Monitoring groups by `ats_provider`; each group has a non-destructive
      master switch (`monitoring.ats.<provider>.enabled`), a monitored-company list
      with unmonitor, and an add-to-monitoring dropdown.
- [ ] Disabling an ATS master switch, then re-loading Settings, shows the provider's
      monitored companies unchanged (flags preserved); a subsequent
      `python scrape.py --monitored-only` skips that provider.
- [ ] Monitoring a company via the dropdown under a currently-disabled ATS flips
      `monitoring.ats.<provider>.enabled` back to `"true"` (cascade re-arm).
- [ ] **Independence:** setting `scraper.greenhouse.enabled=false` removes Greenhouse
      from a broad run but a `--monitored-only` run still scrapes monitored
      Greenhouse companies; setting `monitoring.ats.greenhouse.enabled=false` pauses
      monitored Greenhouse but a broad run still performs Greenhouse discovery. The
      two keys never read each other.
- [ ] `is_monitoring_source_enabled` reads the monitoring key, not the broad key; all
      call sites verified.
- [ ] No change to `models.py`, `storage.py` schema/migrations, `profiles.py`,
      `scorer.py`, `score.py`, `main.py`, `companies.py`, `company_detail.py`, or any
      scraper `fetch()` body.

---

## Suggested phase order for `/speckit-tasks`

1. `scrapers/base.py` — add the two class attributes with board-friendly defaults.
2. Company-keyed scrapers — set `ACQUISITION_MODEL` + per-file `SUPPORTS_DISCOVERY`
   (read each `fetch` to decide the flag). Trivial, mechanical, one commit.
3. `scrape.py` — broad-path discovery skip + monitored-path per-provider gate.
4. `tracker_views/shared.py` — repoint `is_monitoring_source_enabled` (grep callers).
5. `tracker_views/settings.py` — `_render_broad_scraping` + `_render_company_monitoring`
   rewrite + `render()` ordering; delete old toggle grid and crypto name set.
6. Validate: toggle a board off → absent from a broad run; pause an ATS master switch →
   `--monitored-only` skips it and companies stay monitored; monitor a company under a
   paused ATS via the dropdown → switch re-arms; confirm
   `sqlite3 data/jobs.db "SELECT key,value FROM config WHERE key LIKE 'monitoring.ats.%'"`
   reflects the toggles.
