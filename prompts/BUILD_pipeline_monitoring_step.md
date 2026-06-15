# BUILD — Monitoring step in the full pipeline

> Spec for Claude Code. Read `main.py`, `scrape.py`, `storage.py` (config
> helpers + `get_monitored_companies`), and `tracker_views/settings.py`
> (`_render_run_controls`) before starting.
> Surgical edits only — no new dependencies, no schema changes.

---

## Goal

`main.py` currently runs three steps: broad scrape → extract → score. The
`--monitored-only` scrape path (built in Phase 8a's predecessor work) is never
invoked automatically — it only runs when the user types
`python scrape.py --monitored-only` by hand.

This spec wires monitoring into the full pipeline, controllable via a config
setting (Settings UI checkbox) and CLI flags, while keeping the three
existing run modes independently runnable:

1. **Monitoring only** — `scrape.py --monitored-only` (already exists, unchanged)
2. **Broad scrape only** — `scrape.py` (already exists, unchanged)
3. **Full pipeline** — `main.py`, which should now optionally include the
   monitoring scrape as an extra step, gated by a config flag and overridable
   via CLI flags.

---

## 1. `storage.py` — no schema change, just a config convention

No new methods needed. Use the existing `get_config`/`set_config` with a new
key:

```
monitoring.enabled_in_pipeline   "true" | "false"   (default: "true" if unset)
```

Convention: absence of the key means "enabled" (consistent with how
`scraper.<name>.enabled` defaults to enabled when unset). This means existing
installs get monitoring-in-pipeline for free once they have monitored
companies, without a migration.

---

## 2. `main.py` — new step + CLI flags

### New argparse flags

```python
parser.add_argument("--monitored-only", action="store_true",
                     help="Run only the monitored-company scrape step (skip broad scrape), then extract+score")
parser.add_argument("--no-monitoring", action="store_true",
                     help="Skip the monitored-company scrape step even if enabled in config")
```

Mutually exclusive: if both are passed, exit with an error message (same
pattern as `score.py`'s `--extract`/`--profile` mutual exclusion check).

### Step ordering

New step order:

```
Step 0 (conditional): Monitored-company scrape  — scrape.py --monitored-only
Step 1 (conditional): Broad scrape               — scrape.py
Step 2: Extraction                                — score.py --extract
Step 3: Scoring                                   — score.py --profile <active>
```

Renumber the printed step labels accordingly (e.g. "=== Step 1: Monitored
scrape ===", "=== Step 2: Broad scrape ===", "=== Step 3: Extraction ===",
"=== Step 4: Scoring [...] ===") — but only print/run steps that actually
execute, so a `--monitored-only` run shows "Step 1: Monitored scrape", "Step
2: Extraction", "Step 3: Scoring" (broad scrape step omitted entirely, not
just skipped-with-a-message).

### Decision logic

```python
db = JobStorage(DB_PATH)
active_id = db.get_config("active_profile_id", DEFAULT_PROFILE_ID)

if args.monitored_only and args.no_monitoring:
    print("--monitored-only and --no-monitoring are mutually exclusive.")
    sys.exit(1)

run_broad = not args.monitored_only
run_monitoring = False

if args.monitored_only:
    run_monitoring = True
elif not args.no_monitoring:
    cfg = db.get_config("monitoring.enabled_in_pipeline")
    enabled_in_config = cfg is None or cfg.lower() == "true"
    if enabled_in_config:
        has_monitored = bool(db.get_monitored_companies())
        run_monitoring = has_monitored
```

`get_monitored_companies()` already exists in `storage.py` and returns
companies where `monitored = TRUE` — reuse it as-is, no new method.

### Step implementations

Each step is a `subprocess.run([...], check=True)` call, same pattern as
existing steps:

```python
step_num = 1

if run_monitoring:
    print(f"[{_ts()}] === Step {step_num}: Monitored-company scrape ===")
    subprocess.run([sys.executable, "scrape.py", "--monitored-only"], check=True)
    step_num += 1
    print(f"[{_ts()}] Monitored-company scrape done")

if run_broad:
    print(f"[{_ts()}] === Step {step_num}: Broad scrape ===")
    subprocess.run([sys.executable, "scrape.py"], check=True)
    step_num += 1
    print(f"[{_ts()}] Broad scrape done")

print(f"[{_ts()}] === Step {step_num}: Extraction ===")
subprocess.run([sys.executable, "score.py", "--extract"], check=True)
step_num += 1

print(f"[{_ts()}] === Step {step_num}: Scoring [{active_id}] ===")
subprocess.run([sys.executable, "score.py", "--profile", active_id], check=True)
```

Preserve existing timing/elapsed-time print statements (`t0`, `t1`, etc.) —
adapt variable names as needed but keep the "X done — Ys elapsed, Zs total"
format for each step that runs. The final `db.update_last_run(duration_seconds=...)`
and the `except` block's `db.log_run(...)` on error are unchanged.

### Edge case: `--monitored-only` with zero monitored companies

`scrape.py --monitored-only` already handles this gracefully — prints
"Nothing to monitor — no companies with monitored=true." and logs a run with
`run_type="monitored_only"`, then returns cleanly (exit 0). `main.py` doesn't
need special handling here; the subprocess still exits 0 and the pipeline
continues to extraction/scoring as normal (which will find nothing new to
extract/score from that step, but the broad-scrape step — if running — will
still feed it).

---

## 3. `tracker_views/settings.py` — config checkbox + run button

### Checkbox

In `_render_monitored_companies()` (the "🎯 Monitored Companies" panel),
add a checkbox near the top, above the company list:

```python
cfg = db.get_config("monitoring.enabled_in_pipeline")
enabled_in_pipeline = cfg is None or cfg.lower() == "true"

new_val = st.checkbox(
    "Include monitored-company scrape in the full pipeline run",
    value=enabled_in_pipeline,
    key="monitoring_enabled_in_pipeline",
    help="When on and at least one company is monitored, `python main.py` "
         "will also run `scrape.py --monitored-only` as part of the full "
         "pipeline. Independent of this setting, you can always run "
         "monitoring on its own (button below) or via "
         "`scrape.py --monitored-only`.",
)
if new_val != enabled_in_pipeline:
    db.set_config("monitoring.enabled_in_pipeline", "true" if new_val else "false")
    st.rerun()
```

Place this checkbox before the "➕ Add a company to monitor" expander, so it's
visible regardless of whether the company list below is empty.

### Run button

In `_render_run_controls()`, add a third button alongside "🕸 Run scrape" and
"🎯 Run scoring": "🎯 Run monitoring only", using the same `subprocess.Popen`
background-process pattern (session-state keys `bg_process`/`bg_label`/etc.
already generalize across labels via `label.title()`).

```python
c1, c2, c3 = st.columns(3)
with c1:
    # existing "🕸 Run scrape" button — unchanged
with c2:
    if st.button("🎯 Run monitoring only", use_container_width=True,
                 help="Run scrape.py --monitored-only. Fetches all openings "
                      "from monitored companies, independent of the broad scrape."):
        proc = subprocess.Popen(
            [sys.executable, "-u", "scrape.py", "--monitored-only"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=False,
        )
        st.session_state.bg_process = proc
        st.session_state.bg_label = "monitoring"
        st.session_state.bg_output = ""
        st.session_state.bg_start = time.time()
        st.rerun()
with c3:
    # existing "🎯 Run scoring" button — unchanged
```

`label.title()` on `"monitoring"` → `"Monitoring"`, which reads correctly in
the existing status messages ("⏳ **Monitoring** running...", "✅ Monitoring
completed successfully!").

If `get_monitored_companies()` is empty, the button can remain enabled
(running `--monitored-only` with zero monitored companies is a safe, fast
no-op per the existing handling) — no need to disable it.

---

## Explicit non-goals

- No change to `scrape.py` or `score.py` internals — both `--monitored-only`
  and the broad/extract/score paths already work correctly (validated live
  this session).
- No new DB columns, tables, or migrations — `monitoring.enabled_in_pipeline`
  is a plain config key, same mechanism as `scraper.<name>.enabled`.
- Does not change `docker-compose.yml` or cron scheduling — `main.py` remains
  the single entry point invoked by the `agent` service; this spec changes
  what `main.py` does internally.
- Does not add a separate cron entry for a higher-frequency monitoring-only
  schedule — that remains a manual/future option (run
  `python scrape.py --monitored-only` or the new Settings button on demand).

---

## Acceptance criteria

- [ ] `python main.py` with 0 monitored companies behaves identically to
      today (no monitoring step, no extra subprocess call, no new log lines).
- [ ] `python main.py` with ≥1 monitored company and
      `monitoring.enabled_in_pipeline` unset or `"true"`: runs monitored-scrape
      → broad scrape → extract → score, with correctly numbered step labels.
- [ ] `python main.py --no-monitoring` with ≥1 monitored company: skips the
      monitoring step regardless of config, runs broad scrape → extract → score.
- [ ] `python main.py --monitored-only`: runs monitored-scrape → extract →
      score only — broad scrape step is omitted entirely (not just skipped
      with a message).
- [ ] `python main.py --monitored-only --no-monitoring` exits with an error
      and a clear message, no subprocess calls made.
- [ ] Setting `monitoring.enabled_in_pipeline = "false"` via the Settings
      checkbox, then running `python main.py` with ≥1 monitored company: no
      monitoring step runs (broad scrape → extract → score only).
- [ ] Settings checkbox reflects current config value on page load and
      persists across reruns.
- [ ] "🎯 Run monitoring only" button in Settings runs `scrape.py
      --monitored-only` as a background process with the same live-output /
      stop / completion UI as the existing scrape/score buttons.
- [ ] On error in any step, `db.log_run(..., status="error", error_msg=...)`
      is still called with the correct `profile_id` and elapsed time.
