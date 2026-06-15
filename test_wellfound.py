import httpx
import json
import os

from dotenv import load_dotenv

load_dotenv()

response = httpx.get(
    "https://startup-jobs-api.p.rapidapi.com/active-jb-7d",
    headers={
        "X-RapidAPI-Key": os.getenv("X_RAPIDAPI_KEY"),
        "X-RapidAPI-Host": "startup-jobs-api.p.rapidapi.com",
    },
    params={
        "source": "wellfound",
        "title_filter": "product manager",
    },
    timeout=15,
)

print(f"Status: {response.status_code}")
data = response.json()
print(json.dumps(data, indent=2)[:2000])

if isinstance(data, list):
    print(f"\nJobs returned: {len(data)}")
    if data:
        print(f"\nFirst job keys: {list(data[0].keys())}")
elif isinstance(data, dict):
    print(f"\nTop-level keys: {list(data.keys())}")
