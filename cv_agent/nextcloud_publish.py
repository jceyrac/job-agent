"""nextcloud_publish.py — WebDAV publication of rendered files (spec 029, FR-013).

Publishes the locally-rendered ``.json``/``.docx``/``.pdf`` into the user's
Nextcloud "Job applications" folder over WebDAV, so the files reach Nextcloud on
any host. The Mac's desktop-sync client does not exist on the headless verva
server, so the agent pushes explicitly instead of relying on local sync. The
render itself stays 100% local (see ``renderer.py``).

⚠️ No ``llm`` import (factual firewall). Uses only ``httpx`` (already a
dependency). Secrets come from ``CV_NC_*`` env vars — never hardcoded, never
logged. Publication is a no-op (returns ``None``) when ``CV_NC_*`` is unset, so a
pure-local dev run is unaffected.
"""

import os
import sys
import time
from urllib.parse import quote

import httpx

from cv_agent.renderer import application_folder_name

# The fixed remote path under the user's Nextcloud files (matches the on-disk
# convention in research §9 / FR-013). "01 Job" carries a space, so it is
# URL-encoded per segment below.
_APP_FOLDER = "Documents/01 Job/Job applications"

# File extensions published — render always produces all three in the working dir.
_EXTENSIONS = (".json", ".docx", ".pdf")


def publish_application(local_dir: str, company: str, title: str) -> dict | None:
    """PUT the rendered files from ``local_dir`` into Nextcloud via WebDAV.

    Returns ``None`` when the three ``CV_NC_*`` env vars are all absent (publish
    disabled — local dev mode); raises ``RuntimeError`` on a partial set
    (misconfiguration); otherwise returns
    ``{nextcloud_dir_url, web_url, pdf_url, files}``.
    """
    base = os.environ.get("CV_NC_BASE_URL")
    user = os.environ.get("CV_NC_USER")
    app_password = os.environ.get("CV_NC_APP_PASSWORD")

    present = (bool(base), bool(user), bool(app_password))
    if not any(present):
        print("cv_agent:   Nextcloud publish disabled (no CV_NC_* config)",
              file=sys.stderr, flush=True)
        return None
    if not all(present):
        missing = [name for name, ok in zip(
            ("CV_NC_BASE_URL", "CV_NC_USER", "CV_NC_APP_PASSWORD"), present) if not ok]
        raise RuntimeError(
            "Nextcloud publish misconfigured — missing " + ", ".join(missing)
            + " (set all three CV_NC_* or none)"
        )

    folder = application_folder_name(company, title)
    base = base.rstrip("/")
    # WebDAV root for this user's files (username is one URL-encoded path segment).
    dav_root = f"{base}/remote.php/dav/files/{quote(user, safe='')}"

    # Remote folder URL: encode each segment (spaces → %20), keep the '/' separators.
    remote_rel = f"{_APP_FOLDER}/{folder}"
    encoded_rel = quote(remote_rel, safe="/")
    dav_dir = f"{dav_root}/{encoded_rel}"

    auth = httpx.BasicAuth(user, app_password)
    start = time.time()
    with httpx.Client(auth=auth, timeout=30.0) as client:
        # Idempotent MKCOL of each missing folder level, parents before children.
        path = dav_root
        for seg in remote_rel.split("/"):
            path += "/" + quote(seg, safe="")
            r = client.request("MKCOL", path)
            if r.status_code in (201, 405, 301):
                continue  # 201 created; 405/301 already exists → OK
            r.raise_for_status()

        # PUT every rendered file. Re-publishing after a revise loop overwrites.
        files = {}
        for name in sorted(os.listdir(local_dir)):
            if not name.lower().endswith(_EXTENSIONS):
                continue
            fp = os.path.join(local_dir, name)
            if not os.path.isfile(fp):
                continue
            with open(fp, "rb") as f:
                content = f.read()
            url = f"{dav_dir}/{quote(name, safe='')}"
            client.put(url, content=content).raise_for_status()
            files[name] = url

    elapsed = time.time() - start
    print(f"cv_agent:   publishing to Nextcloud… ({elapsed:.1f}s)",
          file=sys.stderr, flush=True)

    # Browser-previewable Files-app URL (dir query URL-encoded, spaces → %20).
    web_url = f"{base}/index.php/apps/files/?dir=/{encoded_rel}"
    pdf_url = next((u for n, u in files.items() if n.lower().endswith(".pdf")), None)

    return {
        "nextcloud_dir_url": dav_dir,
        "web_url": web_url,
        "pdf_url": pdf_url,
        "files": files,
    }
