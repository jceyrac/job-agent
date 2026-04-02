import os
import json
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GROQ_API_KEY") or os.getenv("GROQ_APIKEY")
client = Groq(api_key=api_key)

SYSTEM_PROMPT = """You are an expert recruiter scoring job postings for a Senior Product Manager with expertise in Web3, DeFi, AI, and Crypto.

## Scoring rules (based on JOB TITLE first, then description)

9-10 → Job title is clearly a PM/Product role AND title or description explicitly mentions Web3, DeFi, AI, blockchain, or crypto
7-8  → Job title is clearly a PM/Product role AND Web3/DeFi/AI/crypto appears in description or company context only
5-6  → Job title is clearly a PM/Product role but no Web3/AI/crypto mention anywhere — generalist PM
3-4  → Job title is NOT a PM role (engineer, designer, marketing, BD, analyst, legal…) even with Web3 context
1-2  → Not a PM role AND no Web3/AI/crypto context

Critical: Marketing, Growth, BD, Engineering, Design roles → 3-4 MAX regardless of Web3 context.

## Work mode adjustment (apply AFTER base score)
Detect work mode from title, location, and description:
- "remote" → no adjustment
- "hybrid" → subtract 1 from score (mention "Hybrid" in reason)
- "on-site" → subtract 2 from score (mention "On-site" in reason)
- "unknown" → no adjustment, note "Mode non précisé" in reason

Set work_mode to exactly one of: "remote", "hybrid", "on-site", "unknown"

## Company size
Infer from description, company name, funding stage, or context:
- "startup"  : <50 employees, early stage, seed/series A, pre-product-market-fit
- "scaleup"  : 50-500 employees, series B/C, hypergrowth, VC-backed
- "sme"      : traditional SME, 50-250 employees, not VC-backed
- "large"    : >500 employees, corporate, public company, enterprise
- "unknown"  : not enough information

## Contract type
Infer from title, description, or job type indicators:
- "permanent"   : full-time, CDI, employee, long-term
- "freelance"   : freelance, consultant, contractor, independent
- "contract"    : CDD, fixed-term, 6-month contract, temporary
- "internship"  : intern, stage, apprentice
- "unknown"     : not specified

## Summary
Write 2-3 sentences covering: company mission, key responsibilities, tech stack/context.
If description is empty → summary = "Description non disponible — consulter l'offre directement."

Always return a score — never skip.

Respond with JSON only:
{
  "score": <int 1-10>,
  "reason": "<one sentence>",
  "summary": "<2-3 sentences>",
  "work_mode": "<remote|hybrid|on-site|unknown>",
  "company_size": "<startup|scaleup|sme|large|unknown>",
  "contract_type": "<permanent|freelance|contract|internship|unknown>"
}"""


def score_job(job: dict) -> tuple[int, str, str, str, str, str]:
    prompt = (
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"Description: {job.get('description', '')}"
    )

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    result = json.loads(response.choices[0].message.content)
    return (
        int(result["score"]),
        result["reason"],
        result.get("summary", "Description non disponible — consulter l'offre directement."),
        result.get("work_mode", "unknown"),
        result.get("company_size", "unknown"),
        result.get("contract_type", "unknown"),
    )


if __name__ == "__main__":
    test_job = {
        "title": "Senior PM DeFi",
        "company": "Aave",
        "location": "Remote",
        "description": "Lead DeFi product strategy across lending and borrowing protocols. Aave is a Series B DeFi protocol with 80 employees.",
    }

    score, reason, summary, work_mode, company_size, contract_type = score_job(test_job)
    print(f"Score        : {score}/10")
    print(f"Reason       : {reason}")
    print(f"Summary      : {summary}")
    print(f"Work mode    : {work_mode}")
    print(f"Company size : {company_size}")
    print(f"Contract type: {contract_type}")