"""renderer.py — deterministic CV render wrapper (spec 029, FR-011).

Merges the LLM-tailored content with the master CV's fixed fields, writes the
intermediate ``cv_data_<slug>.json``, then shells out to the ``.cv_pipeline``
render harness (``node render_cv.js``) and LibreOffice. All three deliverables
(``.json``/``.docx``/``.pdf``) are written into a **neutral local working dir**
(``<data>/cv_outputs/<Company> - <Title>`` by default) — publication to Nextcloud
is a separate, host-agnostic WebDAV step (``nextcloud_publish.py``), so the agent
no longer depends on the Mac's desktop-sync client.

⚠️ FACTUAL FIREWALL (research §4, contracts node-io-contract.md): this module
imports **no** ``llm``. The fields it derives are deterministic — fixed master
fields (``photo``, ``relocation``), ``contact`` (CH/FR from ``proposed_profile``)
and ``filename`` (from ``slug``). The LLM never controls output naming, the
contact block, or the photo/relocation fields (Constitution §IV: structure is
deterministic, prose is LLM). The header *subtitle* is prose: it prefers the
human override, then the LLM-adapted title, then the master default.
"""

import json
import os
import re
import subprocess
import sys

from paths import DATA_DIR

# The render harness lives in …/AI-Suite/.cv_pipeline — a *sibling* of this
# repo (note: the spec's "~/.cv_pipeline" was stale; the real dir is here).
# Env-overridable for prod where the harness may live elsewhere.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CV_PIPELINE_DIR = os.environ.get(
    "CV_PIPELINE_DIR",
    os.path.join(os.path.dirname(_REPO_ROOT), ".cv_pipeline"),
)
MASTER_CV_PATH = os.path.join(CV_PIPELINE_DIR, "cv_data_master.json")

def default_output_root() -> str:
    """Neutral local working dir for rendered output (identical on both hosts).

    Rendered files land here locally; publication to Nextcloud is a separate,
    explicit WebDAV step (``nextcloud_publish.py``) so the agent no longer depends
    on the Mac's desktop-sync client. ``CV_OUTPUT_DIR`` env / config
    ``cv.output_dir`` only override this working dir (the caller resolves those,
    since config needs a DB handle this module deliberately does not hold).
    """
    return os.path.join(DATA_DIR, "cv_outputs")

# Fixed-content fields carried from the master into every tailored data file.
_FIXED_FIELDS = ("_note", "photo", "relocation")
# Content fields the LLM may re-angle (with master fallback). "title" is
# handled separately (human override > LLM-adapted title > master default), not
# via this tuple — see render_documents().
_CONTENT_FIELDS = (
    "profile", "competencies", "roles",
    "education", "languages", "interests",
)


def load_master() -> dict:
    """Load the neutral master CV data (the factual source of truth)."""
    with open(MASTER_CV_PATH, encoding="utf-8") as f:
        return json.load(f)


def _filename_from_slug(slug: str) -> str:
    """Derive the deterministic .docx/.pdf basename from the ASCII slug.

    "ceffu-senior-product-manager" → "Jerome_Ceyrac_CV_Ceffu_Senior_Product_Manager".
    """
    name = slug.replace("-", " ").title().replace(" ", "_")
    return f"Jerome_Ceyrac_CV_{name}"


def _soffice() -> str:
    """Resolve the LibreOffice binary: env override → macOS app → PATH."""
    if os.environ.get("SOFFICE"):
        return os.environ["SOFFICE"]
    mac = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if os.path.exists(mac):
        return mac
    return "soffice"


def application_folder_name(company: str, title: str) -> str:
    """The per-application folder name: a sanitised ``<Company> - <Title>``.

    Shared with ``nextcloud_publish`` so the local working folder and the remote
    WebDAV folder always match (one sanitisation, no duplication).
    """
    company = (company or "Company").strip()
    title = (title or "Role").strip()
    return re.sub(r'[\\/:*?"<>|]', "-", f"{company} - {title}").strip()


def _application_dir(job: dict, output_root: str) -> str:
    """The per-application folder under the output root (research §9)."""
    return os.path.join(
        output_root,
        application_folder_name(job.get("company"), job.get("title")),
    )


def render_documents(cv_content: dict, proposed_profile: str, slug: str, job: dict, output_root: str,
                     title_override: str = "") -> dict:
    """Render the tailored CV to .json/.docx/.pdf into the job's subfolder of
    ``output_root``. Returns the output paths dict (``local_dir`` included).

    ``cv_content`` is the LLM-tailored content; this function is the only place
    that assembles the final data file, layering fixed fields + derived fields
    on top of it (never trusting the LLM to name files or set the header).

    The header ``title`` (subtitle) resolution order: the human's
    ``title_override`` when set at the analysis gate, else the LLM-adapted
    ``cv_content.title``, else the master's standard title — so the subtitle can
    be re-angled to the job's domain while a human override always wins.
    """
    master = load_master()

    # Derived, deterministic fields — never LLM-controlled.
    contact = "CH" if proposed_profile != "french" else "FR"
    filename = _filename_from_slug(slug)

    data: dict = {k: master.get(k, "") for k in _FIXED_FIELDS}
    data["contact"] = contact
    data["filename"] = filename
    for key in _CONTENT_FIELDS:
        # Fall back to the master's own content when the LLM left a field empty.
        data[key] = cv_content.get(key) if cv_content.get(key) else master.get(key)
    # Title (subtitle): human override wins, else the LLM-adapted title, else
    # the master default.
    data["title"] = ((title_override or "").strip()
                     or (cv_content.get("title") or "").strip()
                     or master.get("title", ""))

    # The working folder holds all three deliverables (.json/.docx/.pdf); the
    # publish step then pushes them to Nextcloud via WebDAV (host-agnostic).
    outdir = _application_dir(job, output_root)
    os.makedirs(outdir, exist_ok=True)
    data_path = os.path.join(outdir, f"cv_data_{slug}.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 1) Deterministic .docx via the Node harness (node render_cv.js <data> <outdir>).
    render_js = os.path.join(CV_PIPELINE_DIR, "render_cv.js")
    print("cv_agent:   building .docx (node harness)…", file=sys.stderr, flush=True)
    subprocess.run(
        ["node", render_js, data_path, outdir],
        check=True,
        capture_output=True,
    )
    docx = os.path.join(outdir, filename + ".docx")

    # 2) PDF via LibreOffice (headless).
    print("cv_agent:   converting to .pdf (LibreOffice)…", file=sys.stderr, flush=True)
    subprocess.run(
        [_soffice(), "--headless", "--convert-to", "pdf", "--outdir", outdir, docx],
        check=True,
        capture_output=True,
    )
    pdf = os.path.join(outdir, filename + ".pdf")

    return {"json": data_path, "docx": docx, "pdf": pdf, "local_dir": outdir}
