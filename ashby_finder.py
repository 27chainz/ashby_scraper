import asyncio
import re
import urllib.parse
from pathlib import Path
import httpx


SIMPLIFY_SOURCES = [
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2025-Internships/dev/.github/scripts/listings.json",
]


async def discover_company_slugs():
    slugs = set()
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        for url in SIMPLIFY_SOURCES:
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    raw = re.findall(r"jobs\.ashbyhq\.com/([^/\s\"'\\]+)", r.text)
                    for s in raw:
                        slugs.add(urllib.parse.unquote(s))
            except Exception:
                pass
    return slugs


async def fetch_jobs_for_company(client: httpx.AsyncClient, slug: str):
    try:
        r = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if r.status_code == 200:
            jobs = r.json().get("jobs", [])
            for job in jobs:
                job["company"] = slug
            return jobs
    except Exception:
        pass
    return []


async def fetch_all_jobs_chunked(slugs: set, chunk_size: int = 50):
    slug_list = list(slugs)
    all_jobs = []
    limits = httpx.Limits(max_keepalive_connections=50, max_connections=100)

    async with httpx.AsyncClient(limits=limits, timeout=httpx.Timeout(6.0)) as client:
        for i in range(0, len(slug_list), chunk_size):
            chunk = slug_list[i:i + chunk_size]
            tasks = [fetch_jobs_for_company(client, slug) for slug in chunk]
            results = await asyncio.gather(*tasks)
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


async def run():
    loc = input("Filter location (e.g. 'United Kingdom', 'London', press Enter to search all): ").strip()
    dept = input("Filter department (e.g. 'Sales', 'Finance', press Enter to search all): ").strip()
    min_input = input("Minimum match score % (press Enter to search all): ").strip()
    min_score = float(min_input) if min_input else 0.0

    cv_path = Path("cv.txt")
    cv_text = cv_path.read_text(encoding="utf-8") if cv_path.exists() else ""
    cv_keywords = extract_keywords(cv_text)

    print("\nDiscovering companies using AshbyHQ from the web (live)...")
    slugs = await discover_company_slugs()
    print(f"Discovered {len(slugs)} companies using Ashby.")

    print(f"Fetching live jobs from all {len(slugs)} boards in batches...")
    all_jobs = await fetch_all_jobs_chunked(slugs)
    print(f"Fetched {len(all_jobs)} open positions.\n")

    matched = []
    for job in all_jobs:
        location = str(job.get("location") or "Remote / Unspecified")
        department = str(job.get("department") or "Unspecified")

        if loc and loc.lower() not in location.lower() and loc.lower() not in job.get("title", "").lower():
            continue
        if dept and dept.lower() not in department.lower():
            continue

        score = calculate_match_score(cv_keywords, job)
        if score < min_score:
            continue

        matched.append({
            "company": job.get("company", "").capitalize(),
            "title": job.get("title"),
            "department": department,
            "location": location,
            "url": job.get("jobUrl"),
            "score": score
        })

    matched.sort(key=lambda x: x["score"], reverse=True)

    if not matched:
        print("No jobs matched your criteria.")
        return

    top = matched[0]
    print("==============================================")
    print("      🏆 BEST ASHBY JOB MATCH FOR YOU 🏆     ")
    print("==============================================")
    print(f"Company:    {top['company']}")
    print(f"Role:       {top['title']}")
    print(f"Match:      {top['score']}%")
    print(f"Department: {top['department']}")
    print(f"Location:   {top['location']}")
    print(f"Apply Link: {top['url']}")
    print("==============================================\n")

    print(f"--- Top {min(20, len(matched))} matches across {len(slugs)} discovered Ashby companies ---\n")
    for job in matched[:20]:
        print(f"[{job['score']}% Match] {job['company']} - {job['title']}")
        print(f" Department: {job['department']} | Location: {job['location']}")
        print(f" Apply Link: {job['url']}\n")


if __name__ == "__main__":
    asyncio.run(run())
