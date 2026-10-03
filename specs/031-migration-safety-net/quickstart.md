# Quickstart: Migration Safety Net

Runnable validation scenarios. Prerequisites: verva reachable (Tailscale/SSH), `docker compose`
up, repo checked out at `/opt/job-agent` on verva (or run scripts on dev against a snapshot).

## 1. Backup (US1, SC-001)

```bash
# on verva
docker exec job-tracker python scripts/backup_db.py
# expect: manifest written to /app/data/backups/, integrity ok, core table counts listed
```

## 2. Fingerprint determinism (US3, SC-003)

```bash
# on dev, against a snapshot
python scripts/fingerprint.py --db data/jobs.db --as-of 2026-10-03 > /tmp/f1.json
python scripts/fingerprint.py --db data/jobs.db --as-of 2026-10-03 > /tmp/f2.json
diff /tmp/f1.json /tmp/f2.json   # byte-identical (empty diff)
```

## 3. Compare detects a change (US3, SC-004)

```bash
python scripts/fingerprint.py --db data/jobs.db --as-of 2026-10-03 > /tmp/f3.json
# on a test branch, deliberately break a filter, rebuild, run again:
python scripts/fingerprint.py --db data/jobs.db --as-of 2026-10-03 > /tmp/f4.json
python scripts/compare_fingerprints.py /tmp/f3.json /tmp/f4.json
# expect: non-zero exit, difference shown in the feed section
```

## 4. Restore drill (US2, SC-002)

Follow `docs/restore-procedure.md` **drill** mode → a throwaway tracker container serves the
restored DB on a different port (live volume untouched). Record the measured duration.

## 5. Post-deploy check (US4)

```bash
python scripts/health_check.py
# expect: tracker OK + last run status/type/age; non-zero on failure or age > 26h
```

## 6. Definition of done

After any roadmap step, tick every item in `docs/migration-checklist.md` (backup taken, nightly
cron OK, health check OK, manual smoke test, parity green).
