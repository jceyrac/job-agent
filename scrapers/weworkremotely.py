import feedparser
import re
from datetime import date
from time import mktime
from scrapers.base import BaseScraper
from models import JobFilter, JobPosting


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

                description = getattr(entry, "summary", None)
                if description:
                    description = re.sub(r"<[^>]+>", " ", description).strip()
                    description = re.sub(r"\s+", " ", description)

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
                ))
            return jobs
        except Exception as e:
            print(f"[{self.SOURCE_NAME}] Error fetching RSS: {e}")
            return []
