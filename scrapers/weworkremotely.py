import feedparser
import re
from datetime import date
from time import mktime
from scrapers.base import BaseScraper
from models import JobFilter, JobPosting


def _parse_hq(summary_html: str) -> str | None:
    """Extract base_location from WeWorkRemotely's Headquarters field in the summary HTML."""
    m = re.search(r"<strong>Headquarters:</strong>\s*([^<\n]+)", summary_html or "")
    if not m:
        return None
    hq = m.group(1).strip()
    # Strip leading "Remote - ", "Remote, ", "Remote/" etc. to get the actual base country
    hq = re.sub(r"(?i)^remote[\s\-,/]+", "", hq).strip()
    # If multiple options like "United States or Canada", take first
    hq = re.split(r"\s+or\s+", hq)[0].strip()
    # Strip trailing noise like "(North America)", "(Remote)"
    hq = re.sub(r"\s*\([^)]+\)", "", hq).strip()
    # Normalise "USA" → "United States"
    if hq.upper() in ("USA", "US"):
        hq = "United States"
    return hq or None


class WeWorkRemotelyScraper(BaseScraper):
    SOURCE_NAME = "WeWorkRemotely"
    ENABLED = True
    RSS_URL = "https://weworkremotely.com/remote-jobs.rss"

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        try:
            feed = feedparser.parse(self.RSS_URL)
            jobs = []
            for entry in feed.entries:
                posted_date = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    posted_date = date.fromtimestamp(mktime(entry.published_parsed))

                raw_summary = getattr(entry, "summary", None) or ""
                base_location = _parse_hq(raw_summary)
                description = re.sub(r"<[^>]+>", " ", raw_summary).strip()
                description = re.sub(r"\s+", " ", description) if description else None

                tags = [t.get("term", "") for t in getattr(entry, "tags", [])]

                # WWR embeds company in title as "Company: Job Title"
                raw_title = getattr(entry, "title", "")
                match = re.match(r"^(.+?):\s+(.+)$", raw_title)
                if match:
                    company, title = match.group(1).strip(), match.group(2).strip()
                else:
                    company, title = "", raw_title

                # Location is in the `region` field
                # WWR uses "Anywhere in the World" to mean fully remote
                raw_location = (
                    getattr(entry, "region", None)
                    or getattr(entry, "location", None)
                    or "Remote"
                )
                location = "Remote" if raw_location.lower() in ("anywhere in the world", "") else raw_location

                jobs.append(JobPosting(
                    source=self.SOURCE_NAME,
                    title=title,
                    company=company,
                    location=location,
                    url=getattr(entry, "link", ""),
                    posted_date=posted_date,
                    description=description,
                    tags=tags,
                    salary=None,
                    work_mode="remote",
                    base_location=base_location,
                ))
            return jobs
        except Exception as e:
            print(f"[{self.SOURCE_NAME}] Error fetching RSS: {e}")
            return []
