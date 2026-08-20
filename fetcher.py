import asyncio
import re
from pathlib import Path
import httpx

COMPANY_SLUGS = [
    "alchemy", "amplitude", "anyscale", "apollo", "assemblyai", "astronomer",
    "away", "bland", "braze", "capsule", "cartesia", "census", "chroma", "clari",
    "clerk", "clickup", "coda", "coder", "cohere", "confluent", "customerio",
    "dagster", "datadog", "dave", "dbt", "decagon", "deel", "drata", "drift",
    "elevenlabs", "fal", "figma", "fireblocks", "fireworks", "fivetran", "framer",
    "gong", "groq", "gusto", "hashicorp", "heygen", "hightouch", "huggingface",
    "intercom", "klaviyo", "kustomer", "langchain", "lattice", "ledger", "linear",
    "logrocket", "loom", "mercury", "miro", "mistral", "modal", "monday", "neon",
    "notion", "oaknorth", "openai", "opensea", "outreach", "paxos", "perplexity",
    "pillar", "pinecone", "plaid", "planetscale", "posthog", "postman", "prefect",
    "qdrant", "ramp", "remote", "replit", "replicate", "resend", "rippling", "runway",
    "salesloft", "scale", "scaleai", "segment", "sentry", "singlestore", "snyk",
    "snowflake", "sourcegraph", "speechify", "supabase", "superhuman", "synthesia",
    "tavus", "together", "turso", "typeform", "unstructured", "vanta", "vapi",
    "vercel", "voyage", "warp", "weaviate", "wiz", "workato", "zendesk"
]


async def fetch_company_jobs(client: httpx.AsyncClient, company_slug: str):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}"
    try:
        response = await client.get(url, timeout=4.0)
        if response.status_code == 200:
            jobs = response.json().get("jobs", [])
            for job in jobs:
                job["company"] = company_slug
            return jobs
    except Exception:
        pass
    return []


async def fetch_all_jobs(companies: list):
    limits = httpx.Limits(max_keepalive_connections=50, max_connections=100)
    async with httpx.AsyncClient(limits=limits) as client:
        tasks = [fetch_company_jobs(client, company.strip().lower()) for company in companies if company.strip()]
        results = await asyncio.gather(*tasks)
    all_jobs = []
    for job_list in results:
        all_jobs.extend(job_list)
    return all_jobs


def extract_keywords(text: str):
    words = re.findall(r"\b[a-zA-Z0-9+#.-]{2,}\b", text.lower())
    stop_words = {
        "and", "the", "for", "with", "that", "this", "from", "are", "have", "you",
        "will", "our", "all", "can", "has", "not", "but", "was", "been", "work"
    }
    return {w for w in words if w not in stop_words}


def calculate_match_score(cv_keywords: set, job: dict):
    title = job.get("title", "")
    description = job.get("descriptionPlain", "")
    job_text = f"{title} {description}".lower()

    if not cv_keywords:
        return 0.0

    matched = [kw for kw in cv_keywords if kw in job_text]
    title_matches = [kw for kw in cv_keywords if kw in title.lower()]

    score = (len(matched) / len(cv_keywords)) * 100
    if title_matches:
        score += len(title_matches) * 10

    return min(round(score, 1), 100.0)


def match_jobs(
    cv_file_path: str = "cv.txt",
    location_filter: str = "",
    department_filter: str = "",
    min_score: float = 0.0,
    extra_companies: list = None
):
    companies = list(COMPANY_SLUGS)
    if extra_companies:
        companies.extend(extra_companies)

    cv_path = Path(cv_file_path)
    if cv_path.exists():
        cv_text = cv_path.read_text(encoding="utf-8")
    else:
        cv_text = ""

    cv_keywords = extract_keywords(cv_text)
    print(f"Scouring {len(companies)} company job boards across AshbyHQ...")
    jobs = asyncio.run(fetch_all_jobs(companies))
    print(f"Total open jobs fetched: {len(jobs)}")

    results = []
    for job in jobs:
        location = str(job.get("location") or "Remote / Unspecified")
        department = str(job.get("department") or "Unspecified")

        if location_filter and location_filter.lower() not in location.lower() and location_filter.lower() not in job.get("title", "").lower():
            continue

        if department_filter and department_filter.lower() not in department.lower():
            continue

        score = calculate_match_score(cv_keywords, job)
        if score < min_score:
            continue

        results.append({
            "company": job.get("company", "").capitalize(),
            "title": job.get("title"),
            "department": department,
            "location": location,
            "url": job.get("jobUrl"),
            "score": score
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


if __name__ == "__main__":
    location = input("Filter location (e.g. 'United Kingdom', 'London', press Enter to search all): ").strip()
    department = input("Filter department (e.g. 'Sales', 'Engineering', press Enter to search all): ").strip()
    min_score_input = input("Minimum match score % (press Enter to search all): ").strip()
    min_score = float(min_score_input) if min_score_input else 0.0
    extra = input("Add any specific companies (comma separated, e.g. 'stripe, stripe-inc', press Enter to skip): ").strip()
    extra_companies = [c.strip() for c in extra.split(",")] if extra else []

    matched_jobs = match_jobs(
        location_filter=location,
        department_filter=department,
        min_score=min_score,
        extra_companies=extra_companies
    )

    if not matched_jobs:
        print("\nNo jobs matched your criteria.")
    else:
        top_job = matched_jobs[0]
        print("\n==============================================")
        print("         🏆 OVERALL BEST JOB MATCH 🏆        ")
        print("==============================================")
        print(f"Company:    {top_job['company']}")
        print(f"Role:       {top_job['title']}")
        print(f"Match:      {top_job['score']}%")
        print(f"Department: {top_job['department']}")
        print(f"Location:   {top_job['location']}")
        print(f"Apply Link: {top_job['url']}")
        print("==============================================\n")

        print(f"--- Top {min(20, len(matched_jobs))} Matches Across All Companies ---\n")
        for job in matched_jobs[:20]:
            print(f"[{job['score']}% Match] {job['company']} - {job['title']}")
            print(f" Department: {job['department']} | Location: {job['location']}")
            print(f" Apply Link: {job['url']}\n")