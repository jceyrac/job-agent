#!/usr/bin/env python3
"""
email_monitor.py --- Monitor recruiter emails via Hydroxide IMAP bridge.

Connects to Hydroxide IMAP (ProtonMail bridge), fetches unseen emails,
classifies them using Groq LLM, and auto-updates job_tracking status for
matched companies.

Usage:
  python email_monitor.py --dry-run        # test with fake emails (no IMAP)
  python email_monitor.py --once           # single IMAP pass (for cron)
  python email_monitor.py --interval 300   # long-running daemon
"""

import argparse
import email
import imaplib
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from email.header import decode_header
from typing import Optional

from dotenv import load_dotenv
from groq import Groq

from storage import JobStorage

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [email-monitor] %(levelname)s %(message)s",
)
logger = logging.getLogger("email_monitor")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HYDROXIDE_HOST = os.getenv("HYDROXIDE_IMAP_HOST", "host.docker.internal")
HYDROXIDE_PORT = int(os.getenv("HYDROXIDE_IMAP_PORT", "1143"))
HYDROXIDE_PASSWORD = os.getenv("HYDROXIDE_PASSWORD", "")
HYDROXIDE_EMAIL = os.getenv("HYDROXIDE_EMAIL", "jceyrac@pm.me")
GROQ_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("GROQ_APIKEY")

CLASSIFICATION_MODEL = "llama-3.1-8b-instant"

# ---------------------------------------------------------------------------
# Classification prompt
# ---------------------------------------------------------------------------

CLASSIFY_PROMPT = """Analyze this email about a job application and return ONLY valid JSON.
No explanation, no markdown, just JSON.

{
  "company": "<company name extracted from email, or null>",
  "status": "rejected" | "interview_scheduled" | "offer" | "follow_up" | "unknown",
  "confidence": "high" | "low",
  "reason": "<one sentence>"
}

Rules:
- "rejected": clear rejection language ("unfortunately", "not moving forward", etc.)
- "interview_scheduled": invitation to interview, call, or assessment
- "offer": job offer, contract sent
- "follow_up": automated acknowledgement, "we received your application"
- "unknown": anything else

Email sender: {sender}
Email subject: {subject}
Email body (first 1000 chars): {body}"""

# ---------------------------------------------------------------------------
# Dry-run test emails
# ---------------------------------------------------------------------------

DRY_RUN_EMAILS = [
    {
        "sender": "recruiter@acme-corp.com",
        "subject": "Interview Invitation: Senior PM Role at Acme Corp",
        "body": (
            "Dear Candidate,\n\n"
            "Thank you for your application. We are pleased to invite you "
            "to an interview for the Senior Product Manager position at "
            "Acme Corp. Please let us know your availability next week.\n\n"
            "Best regards,\nAcme Corp Talent Team"
        ),
    },
    {
        "sender": "no-reply@cryptostartup.io",
        "subject": "Update on your application",
        "body": (
            "Dear Applicant,\n\n"
            "Thank you for your interest in the Blockchain PM role at "
            "CryptoStartup. Unfortunately, we have decided to move forward "
            "with other candidates at this time.\n\n"
            "We wish you the best in your search.\n\n"
            "Regards,\nCryptoStartup Hiring"
        ),
    },
    {
        "sender": "jobs@defiprotocol.fi",
        "subject": "We received your application",
        "body": (
            "Hello,\n\n"
            "This is an automated confirmation that we have received your "
            "application for the DeFi Product Manager position at "
            "DeFi Protocol. Our team will review it and get back to you.\n\n"
            "Thank you,\nDeFi Protocol Careers"
        ),
    },
    {
        "sender": "newsletter@techweekly.com",
        "subject": "Top 10 PM Jobs This Week",
        "body": (
            "This week's curated list of product management roles across "
            "Web3 and fintech. Click to view the full list..."
        ),
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode_header_value(value) -> str:
    if value is None:
        return ""
    parts = decode_header(value)
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            try:
                result.append(part.decode(charset or "utf-8", errors="replace"))
            except (LookupError, UnicodeDecodeError):
                result.append(part.decode("utf-8", errors="replace"))
        else:
            result.append(str(part))
    return " ".join(result)


def _get_email_body(msg: email.message.Message) -> str:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        body += payload.decode(charset, errors="replace")
                    except (LookupError, UnicodeDecodeError):
                        body += payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                body = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                body = payload.decode("utf-8", errors="replace")
    return body[:1000] if body else ""


# ---------------------------------------------------------------------------
# Email monitor
# ---------------------------------------------------------------------------

STATUS_MAP = {
    "rejected":            "rejected",
    "interview_scheduled": "interviewing",
    "offer":               "offer",
    "follow_up":           None,       # no status change, just log
    "unknown":             None,       # skip
}

EMOJI_MAP = {
    "set_interviewing": "🎯",
    "set_rejected":     "❌",
    "set_offer":        "🏆",
    "logged_follow_up": "✅",
}


class EmailMonitor:
    """Monitors Hydroxide IMAP inbox and processes unseen recruiter emails."""

    def __init__(self, db: JobStorage):
        self.db = db
        self.groq = Groq(api_key=GROQ_API_KEY, max_retries=1) if GROQ_API_KEY else None

    def _classify_email(self, sender: str, subject: str, body: str) -> Optional[dict]:
        if not self.groq:
            logger.warning("No Groq API key configured; skipping classification")
            return None
        try:
            response = self.groq.chat.completions.create(
                model=CLASSIFICATION_MODEL,
                messages=[{
                    "role": "user",
                    "content": (CLASSIFY_PROMPT
                        .replace("{sender}", sender)
                        .replace("{subject}", subject)
                        .replace("{body}", body)),
                }],
                temperature=0.0,
                max_tokens=200,
                response_format={"type": "json_object"},
            )
            text = response.choices[0].message.content
            return json.loads(text)
        except Exception as exc:
            logger.error(f"Classification error: {exc}")
            return None

    def process_email(self, sender: str, subject: str, body: str) -> dict:
        """Classify and process a single email. Returns summary dict."""
        result = {"sender": sender, "subject": subject,
                  "status": "unknown", "action": "none"}

        if not subject or len(subject.strip()) < 2:
            return result

        classification = self._classify_email(sender, subject, body)
        if classification is None:
            result["action"] = "error"
            return result

        email_status = classification.get("status", "unknown")
        confidence = classification.get("confidence", "low")
        company_name = classification.get("company")
        reason = classification.get("reason", "")

        result["status"] = email_status
        result["confidence"] = confidence
        result["company"] = company_name
        result["reason"] = reason

        # Only act on high-confidence classifications with a company name
        if confidence != "high":
            result["action"] = "skipped_low_confidence"
            return result

        if email_status == "unknown":
            result["action"] = "skipped_unknown"
            return result

        target_status = STATUS_MAP.get(email_status)
        if target_status is None and email_status == "follow_up":
            target_status = None  # explicit: follow_up is log-only

        if not company_name:
            result["action"] = "skipped_no_company"
            return result

        # Find matching applied jobs
        jobs = self.db.find_jobs_by_company(company_name)
        result["matched_jobs"] = len(jobs)

        if len(jobs) != 1:
            result["action"] = f"skipped_unmatched_{len(jobs)}"
            return result

        job = jobs[0]
        job_id = job["id"]
        job_title = job.get("title", "")

        # Take action
        if email_status in ("rejected", "interview_scheduled", "offer"):
            self.db.set_status(job_id, target_status)
            result["action"] = f"set_{target_status}"
        elif email_status == "follow_up":
            result["action"] = "logged_follow_up"

        logger.info(
            f"{EMOJI_MAP.get(result['action'], 'ℹ️')} {email_status}: "
            f"\"{subject[:80]}\" @ {company_name} ({job_title}) "
            f"→ {result['action']}"
        )

        return result

    def run_dry_run(self) -> None:
        """Process hardcoded test emails, print results without DB writes."""
        logger.info("=== DRY RUN: testing classification logic (no IMAP, no DB writes) ===")
        for i, test_email in enumerate(DRY_RUN_EMAILS):
            print(f"\n--- Test email {i + 1} ---")
            print(f"From:    {test_email['sender']}")
            print(f"Subject: {test_email['subject']}")

            classification = self._classify_email(
                test_email["sender"],
                test_email["subject"],
                test_email["body"],
            )
            if classification:
                print(f"Result:  {json.dumps(classification, indent=2)}")

                # Show what would happen (skip if no company or unknown)
                company = classification.get("company")
                confidence = classification.get("confidence", "low")
                email_status = classification.get("status", "unknown")

                if confidence == "high" and company and email_status != "unknown":
                    jobs = self.db.find_jobs_by_company(company)
                    print(f"DB match: {len(jobs)} applied job(s) for \"{company}\"")
                    for j in jobs:
                        print(f"  - {j['title']} ({j['status']})")
                    target = STATUS_MAP.get(email_status)
                    if len(jobs) == 1 and target:
                        print(f"WOULD {status_change(target)}: {jobs[0]['title']}")
                    elif len(jobs) == 0:
                        print("WOULD skip: no matching applied job in DB")
                    else:
                        print(f"WOULD skip: ambiguous ({len(jobs)} matches)")
                elif not company:
                    print("WOULD skip: no company extracted")
                elif confidence != "high":
                    print("WOULD skip: low confidence")
                else:
                    print("WOULD skip: unknown status")
            else:
                print("Result:  ERROR (classification failed)")
        print("\n=== Dry run complete ===")


def status_change(target: str) -> str:
    return {
        "rejected":     "set_status → rejected",
        "interviewing": "set_status → interviewing",
        "offer":        "set_status → offer",
    }.get(target, f"unknown action: {target}")


# ---------------------------------------------------------------------------
# IMAP mode
# ---------------------------------------------------------------------------

class IMAPMonitor(EmailMonitor):
    """Email monitor with IMAP connection (production mode)."""

    def run_once(self) -> dict:
        stats = {"processed": 0, "actions": {}, "errors": 0}

        if not HYDROXIDE_PASSWORD:
            logger.error("HYDROXIDE_PASSWORD not set --- cannot connect to IMAP")
            return stats

        try:
            mail = imaplib.IMAP4_SSL(HYDROXIDE_HOST, HYDROXIDE_PORT)
            mail.login(HYDROXIDE_EMAIL, HYDROXIDE_PASSWORD)
            mail.select("INBOX")

            status, data = mail.search(None, "UNSEEN")
            if status != "OK":
                logger.warning(f"IMAP SEARCH failed: {status}")
                mail.logout()
                return stats

            uids = data[0].split()
            if not uids:
                mail.logout()
                return stats

            logger.info(f"Found {len(uids)} unseen email(s)")

            for uid in uids:
                try:
                    status, msg_data = mail.fetch(uid, "(RFC822)")
                    if status != "OK":
                        continue

                    raw_email = msg_data[0][1]
                    msg = email.message_from_bytes(raw_email)
                    sender = _decode_header_value(msg["From"])
                    subject = _decode_header_value(msg["Subject"])
                    body = _get_email_body(msg)

                    result = self.process_email(sender, subject, body)
                    stats["processed"] += 1
                    action = result.get("action", "none")
                    stats["actions"][action] = stats["actions"].get(action, 0) + 1

                    mail.store(uid, "+FLAGS", "\\Seen")

                except Exception as exc:
                    logger.error(f"Error processing email UID {uid.decode()}: {exc}")
                    stats["errors"] += 1

            mail.close()
            mail.logout()

        except imaplib.IMAP4.error as exc:
            logger.error(f"IMAP connection error: {exc}")
            stats["errors"] += 1
        except Exception as exc:
            logger.error(f"Unexpected error: {exc}")
            stats["errors"] += 1

        return stats

    def run_loop(self, interval: int = 300):
        logger.info(
            f"Starting email monitor --- polling every {interval}s "
            f"({HYDROXIDE_EMAIL} via {HYDROXIDE_HOST}:{HYDROXIDE_PORT})"
        )
        running = True

        def _shutdown(signum, frame):
            nonlocal running
            logger.info("Shutting down...")
            running = False

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        while running:
            start = time.time()
            try:
                stats = self.run_once()
                if stats["processed"] > 0:
                    logger.info(
                        f"Cycle complete: {stats['processed']} processed, "
                        f"actions: {stats['actions']}, {stats['errors']} errors"
                    )
            except Exception as exc:
                logger.error(f"Cycle failed: {exc}")

            elapsed = time.time() - start
            if elapsed < interval and running:
                time.sleep(interval - elapsed)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Monitor recruiter emails via Hydroxide IMAP"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Process hardcoded test emails (no IMAP, no DB writes)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run one IMAP pass and exit (for cron)",
    )
    parser.add_argument(
        "--interval", type=int, default=300,
        help="Polling interval in seconds (default: 300)",
    )
    parser.add_argument(
        "--db", default="data/jobs.db",
        help="Path to SQLite database",
    )
    args = parser.parse_args()

    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY env variable is required for classification")
        sys.exit(1)

    db = JobStorage(args.db)

    if args.dry_run:
        monitor = EmailMonitor(db)
        monitor.run_dry_run()
        return

    if not HYDROXIDE_PASSWORD:
        logger.error(
            "HYDROXIDE_PASSWORD not set --- cannot connect to IMAP. "
            "Use --dry-run for local testing."
        )
        sys.exit(1)

    monitor = IMAPMonitor(db)

    if args.once:
        stats = monitor.run_once()
        logger.info(f"Single run complete: {stats}")
    else:
        monitor.run_loop(interval=args.interval)


if __name__ == "__main__":
    main()
