# Quickstart — CV + Cover Letter Agent (029)

Run the agent end-to-end against real data to prove the feature (§VI empirical
validation). Prerequisites: `.venv` with `langgraph` installed, a configured LLM
key (`LLM_API_KEY`/`DEEPSEEK_API_KEY`), Node + `docx` in `…/AI-Suite/.cv_pipeline/`,
and LibreOffice for PDF.

## Prerequisites

```bash
.venv/bin/pip install "langgraph>=1.2,<2" "langgraph-checkpoint-sqlite>=3.0,<4"
# render harness deps (already present in the repo's .cv_pipeline)
(cd ../.cv_pipeline && npm install)          # if node_modules is missing
```

### Output location + Nextcloud publication (host-agnostic)

Rendered `.json`/`.docx`/`.pdf` land in a neutral local working folder
(`<Company> - <Title>`) under `<data>/cv_outputs` by default (override via
`CV_OUTPUT_DIR` env or config `cv.output_dir`). After render, the `publish` node
pushes them to Nextcloud over WebDAV — set the three `CV_NC_*` env vars
(`CV_NC_BASE_URL`, `CV_NC_USER`, `CV_NC_APP_PASSWORD`) to enable it; leave them
unset for a pure-local run. No desktop client is needed on either host.

## Validation scenarios (from spec "Acceptance scenarios")

### 1. Existing scored `job_id`

```bash
.venv/bin/python -m cv_agent.cli <job_id>   # pick a job with score ≥ 5 in jobs.db
```
- **analysis_gate**: `proceed` → **approval_gate**: `approve`.
- **Expect**: a `.docx` + `.pdf` in the local working folder
  `<data>/cv_outputs/<Company> - <Title>/`; with `CV_NC_*` set, the same files
  are also published to `Documents/01 Job/Job applications/<Company> - <Title>/`
  on Nextcloud — the job status becomes `ready`, and stdout ends with
  `CV_AGENT_RESULT ok <pdf>`.

### 2. Live careers/ATS URL

```bash
.venv/bin/python -m cv_agent.cli <careers-url>
```
- **Expect**: extraction + scoring run through the scorer, the job is persisted
  under the active profile (re-run the same URL → no duplicate, same `job_id`),
  and a tailored CV is produced.

### 3. Closed board → `--paste`

```bash
.venv/bin/python -m cv_agent.cli --paste --letter <<'EOF'
<paste the posting text>
EOF
```
- **Expect**: proceeds identically from `extract_requirements` onward.

### 4. Poor fit → `abort`

```bash
.venv/bin/python -m cv_agent.cli <job_id>   # choose `abort` at analysis_gate
```
- **Expect**: clean exit, job marked `archived` with the `skipped` note, **no**
  documents generated, `CV_AGENT_RESULT aborted <job_id>`.

### 5. Letter not required and not requested

```bash
.venv/bin/python -m cv_agent.cli <job_id>   # a posting without letter_required
```
- **Expect**: `draft_cover_letter` is skipped; only the CV is produced.

### 6. Revise loop bounded at 3

Force revision by rejecting at `approval_gate` repeatedly.
- **Expect**: after 3 revisions the loop stops and proceeds to render with the
  best draft (or forces a decision), never unbounded.

## Automated checks (non-LM, run before commit)

```bash
.venv/bin/python -m pytest tests/                 # 162 storage tests still green
grep -rEn "bind_tools|ChatOpenAI|tools\.py" cv_agent/ || echo "OK: none"
grep -rEn "import llm" cv_agent/renderer.py || echo "OK: renderer imports no llm"
grep -rEn "import llm" cv_agent/nextcloud_publish.py || echo "OK: nextcloud_publish imports no llm"
grep -n "publish" cv_agent/graph.py || echo "MISSING publish node"
grep -rEn "score\s*=|score:" cv_agent/nodes.py || echo "OK: analyze_and_plan emits no score"
```
