import asyncio
import json
import re
import urllib.parse
import webbrowser
from pathlib import Path
import httpx

SIMPLIFY_SOURCES = [
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2025-Internships/dev/.github/scripts/listings.json",
]

MANUAL_ASHBY_SLUGS = {
    "griffin", "ramp", "brex", "linear", "vanta", "multiverse", "synthesia",
    "elevenlabs", "mistral", "cohere", "qdrant", "figma", "retool", "resend",
    "posthog", "checkly", "fly-io", "render", "clerk", "dub", "cal-com"
}

MANUAL_GREENHOUSE_SLUGS = {
    "monzo", "revolut", "stripe", "deliveroo", "starlingbank", "klarna", "n26",
    "wise", "oaknorth", "checkout", "zepz", "gocardless", "doordash", "robinhood",
    "coinbase", "plaid", "gusto", "chime", "affirm"
}

MANUAL_LEVER_SLUGS = {
    "palantir", "spotify", "scaleai", "airtable", "datadog", "dropbox",
    "atlassian", "cloudflare"
}


def strip_html(html_text: str):
    if not html_text:
        return ""
    text = re.sub(r'<[^>]+>', ' ', html_text)
    return ' '.join(text.split())


async def discover_all_ats_slugs():
    ashby = set(MANUAL_ASHBY_SLUGS)
    greenhouse = set(MANUAL_GREENHOUSE_SLUGS)
    lever = set(MANUAL_LEVER_SLUGS)

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        for url in SIMPLIFY_SOURCES:
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    text = r.text
                    ashby.update(urllib.parse.unquote(s) for s in re.findall(r"jobs\.ashbyhq\.com/([^/\s\"'\\\?]+)", text))
                    greenhouse.update(urllib.parse.unquote(s) for s in re.findall(r"boards\.greenhouse\.io/([^/\s\"'\\\?]+)", text))
                    greenhouse.update(urllib.parse.unquote(s) for s in re.findall(r"boards\.eu\.greenhouse\.io/([^/\s\"'\\\?]+)", text))
                    lever.update(urllib.parse.unquote(s) for s in re.findall(r"jobs\.lever\.co/([^/\s\"'\\\?]+)", text))
            except Exception:
                pass
    return ashby, greenhouse, lever


async def fetch_ashby_jobs(client: httpx.AsyncClient, slug: str):
    try:
        r = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if r.status_code == 200:
            jobs = r.json().get("jobs", [])
            normalized = []
            for j in jobs:
                normalized.append({
                    "company": slug,
                    "title": j.get("title", ""),
                    "department": j.get("department") or "General",
                    "location": str(j.get("location") or "Unspecified"),
                    "jobUrl": j.get("jobUrl"),
                    "descriptionPlain": (j.get("descriptionPlain") or "")[:1200],
                    "isRemote": j.get("isRemote", False),
                    "workplaceType": j.get("workplaceType", ""),
                    "ats": "Ashby"
                })
            return normalized
    except Exception:
        pass
    return []


async def fetch_greenhouse_jobs(client: httpx.AsyncClient, slug: str):
    try:
        r = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
        if r.status_code == 200:
            jobs = r.json().get("jobs", [])
            normalized = []
            for j in jobs:
                depts = j.get("departments", [])
                dept_name = depts[0].get("name") if depts else "General"
                location_name = j.get("location", {}).get("name") if isinstance(j.get("location"), dict) else str(j.get("location") or "Unspecified")
                normalized.append({
                    "company": slug,
                    "title": j.get("title", ""),
                    "department": dept_name or "General",
                    "location": location_name,
                    "jobUrl": j.get("absolute_url"),
                    "descriptionPlain": strip_html(j.get("content", ""))[:1200],
                    "isRemote": "remote" in location_name.lower(),
                    "workplaceType": "remote" if "remote" in location_name.lower() else "onsite",
                    "ats": "Greenhouse"
                })
            return normalized
    except Exception:
        pass
    return []


async def fetch_lever_jobs(client: httpx.AsyncClient, slug: str):
    try:
        r = await client.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if r.status_code == 200:
            jobs = r.json()
            if isinstance(jobs, list):
                normalized = []
                for j in jobs:
                    cats = j.get("categories", {}) or {}
                    location_name = cats.get("location") or "Unspecified"
                    workplace = j.get("workplaceType") or ""
                    normalized.append({
                        "company": slug,
                        "title": j.get("text", ""),
                        "department": cats.get("department") or "General",
                        "location": location_name,
                        "jobUrl": j.get("hostedUrl"),
                        "descriptionPlain": (j.get("descriptionPlain") or "")[:1200],
                        "isRemote": "remote" in location_name.lower() or workplace.lower() == "remote",
                        "workplaceType": workplace,
                        "ats": "Lever"
                    })
                return normalized
    except Exception:
        pass
    return []


async def fetch_all_ats_jobs(ashby_slugs: set, gh_slugs: set, lever_slugs: set, chunk_size: int = 50):
    all_jobs = []
    limits = httpx.Limits(max_keepalive_connections=100, max_connections=150)

    async with httpx.AsyncClient(limits=limits, timeout=httpx.Timeout(8.0)) as client:
        ashby_list = list(ashby_slugs)
        for i in range(0, len(ashby_list), chunk_size):
            chunk = ashby_list[i:i + chunk_size]
            tasks = [fetch_ashby_jobs(client, s) for s in chunk]
            results = await asyncio.gather(*tasks)
            for jlist in results:
                all_jobs.extend(jlist)

        gh_list = list(gh_slugs)
        for i in range(0, len(gh_list), chunk_size):
            chunk = gh_list[i:i + chunk_size]
            tasks = [fetch_greenhouse_jobs(client, s) for s in chunk]
            results = await asyncio.gather(*tasks)
            for jlist in results:
                all_jobs.extend(jlist)

        lever_list = list(lever_slugs)
        for i in range(0, len(lever_list), chunk_size):
            chunk = lever_list[i:i + chunk_size]
            tasks = [fetch_lever_jobs(client, s) for s in chunk]
            results = await asyncio.gather(*tasks)
            for jlist in results:
                all_jobs.extend(jlist)

    return all_jobs


def extract_keywords(text: str):
    words = re.findall(r"\b[a-zA-Z0-9+#.-]{2,}\b", text.lower())
    stop_words = {
        "and", "the", "for", "with", "that", "this", "from", "are", "have", "you",
        "will", "our", "all", "can", "has", "not", "but", "was", "been", "work"
    }
    return {w for w in words if w not in stop_words}


def classify_workplace(job: dict):
    location = str(job.get("location") or "")
    title = str(job.get("title") or "")
    description = str(job.get("descriptionPlain") or "")
    workplace_type_attr = str(job.get("workplaceType") or "")
    full_head = f"{title} {location} {workplace_type_attr}".lower()
    full_text = f"{full_head} {description[:400]}".lower()

    if "hybrid" in full_head or "hybrid" in full_text or "in office" in full_text or "days a week" in full_text or "days in" in full_text:
        return "Hybrid", "hybrid"

    if bool(job.get("isRemote", False)) or workplace_type_attr.lower() == "remote" or "fully remote" in full_text or "100% remote" in full_text or "remote -" in location.lower() or "(remote)" in location.lower() or location.lower() == "remote":
        return "Fully Remote", "remote"

    if "remote" in location.lower() or "remote" in title.lower():
        return "Fully Remote", "remote"

    return "On-Site / In-Office", "onsite"


def classify_career_stage(title: str, full_text: str, years_num):
    title_lower = title.lower()

    exec_patterns = [
        r"\bvp\b", r"\bvice president\b", r"\bdirector\b", r"\bhead of\b", r"\bchief\b",
        r"\bexecutive\b", r"\bc-suite\b", r"\bpartner\b", r"\bpresident\b"
    ]
    if any(re.search(pat, title_lower) for pat in exec_patterns):
        return "Executive & Director", "exec", "👑 Exec & Director"

    student_patterns = [
        r"\bintern\b", r"\binternship\b", r"\bplacement\b", r"\bco-op\b", r"\bcoop\b",
        r"\bworking student\b", r"\bstudent\b", r"\bapprentice\b", r"\bapprenticeship\b"
    ]
    if any(re.search(pat, title_lower) for pat in student_patterns) and "internal" not in title_lower and "international" not in title_lower:
        return "Internship / Student", "intern", "🎓 Intern & Placement"

    senior_patterns = [
        r"\bsenior\b", r"\bsr\.\b", r"\bsr\b", r"\bstaff\b", r"\bprincipal\b", r"\blead\b", r"\bmanager\b"
    ]
    if any(re.search(pat, title_lower) for pat in senior_patterns):
        return "Senior Level", "senior", "👔 Senior Level"

    entry_patterns = [
        r"\bjunior\b", r"\bassociate\b", r"\bgraduate\b", r"\bnew grad\b", r"\bbdr\b", r"\bsdr\b",
        r"\btrainee\b", r"\bassistant\b", r"\bentry level\b", r"\bentry-level\b"
    ]
    if any(re.search(pat, title_lower) for pat in entry_patterns):
        return "Entry-Level / Junior", "entry", "🌱 Entry-Level"

    if years_num is not None:
        if years_num <= 2:
            return "Entry-Level / Junior", "entry", "🌱 Entry-Level"
        elif years_num <= 5:
            return "Mid-Level", "mid", "💼 Mid-Level"
        elif years_num <= 8:
            return "Senior Level", "senior", "👔 Senior Level"
        else:
            return "Executive & Director", "exec", "👑 Exec & Director"

    return "Mid-Level", "mid", "💼 Mid-Level"


def detect_language_requirements(job_text: str, user_languages: set):
    text_lower = job_text.lower()

    lang_map = {
        "german": ["german", "deutsch", "fließend deutsch"],
        "french": ["french", "français", "francais"],
        "spanish": ["spanish", "español", "espanol"],
        "italian": ["italian", "italiano"],
        "mandarin": ["mandarin", "chinese"],
        "japanese": ["japanese"],
        "swedish": ["swedish"],
        "danish": ["danish"],
        "norwegian": ["norwegian"],
        "finnish": ["finnish"],
        "portuguese": ["portuguese"]
    }

    required_foreign = []
    for lang, triggers in lang_map.items():
        if any(re.search(r"\b" + re.escape(t) + r"\b", text_lower) for t in triggers):
            if any(req_kw in text_lower for req_kw in ["fluent", "native", "speaking", "required", "language", "speaker", "must speak", "level c1", "level c2", "b2"]):
                required_foreign.append(lang.capitalize())

    unsupported = [l for l in required_foreign if l.lower() not in user_languages and l.lower() != "dutch"]
    return required_foreign, unsupported


def parse_job_metadata(job: dict):
    title = job.get("title", "") or ""
    description = job.get("descriptionPlain", "") or ""
    location = str(job.get("location") or "Unspecified")
    full_text = f"{title} {description}".lower()

    workplace_label, workplace_code = classify_workplace(job)

    country = "Other"
    loc_lower = location.lower()
    if any(k in loc_lower for k in ["uk", "united kingdom", "london", "manchester", "birmingham", "england", "edinburgh", "bristol", "cambridge"]):
        country = "United Kingdom"
    elif any(k in loc_lower for k in ["us", "usa", "united states", "new york", "san francisco", "austin", "california", "texas", "seattle", "boston"]):
        country = "United States"
    elif any(k in loc_lower for k in ["canada", "toronto", "vancouver", "montreal"]):
        country = "Canada"
    elif any(k in loc_lower for k in ["germany", "berlin", "munich", "frankfurt"]):
        country = "Germany"
    elif any(k in loc_lower for k in ["france", "paris"]):
        country = "France"
    elif any(k in loc_lower for k in ["netherlands", "amsterdam"]):
        country = "Netherlands"
    elif workplace_code == "remote":
        country = "Remote Worldwide"

    exp_matches = re.findall(r"(\b\d+\s*-\s*\d+|\b\d+\+?)\s*years?(?:\s*of)?\s*(?:relevant\s*|direct\s*|professional\s*|work\s*)?exp", full_text, re.IGNORECASE)

    exp_text = "Not Specified"
    years_num = None
    if exp_matches:
        raw_match = exp_matches[0]
        exp_text = f"{raw_match} yrs exp"
        num_search = re.search(r"\d+", raw_match)
        if num_search:
            years_num = int(num_search.group())
    elif any(term in full_text for term in ["no experience", "entry level", "graduate", "intern", "placement"]):
        exp_text = "0-1 yrs (Entry Level)"
        years_num = 0

    stage_label, stage_code, stage_badge = classify_career_stage(title, full_text, years_num)

    return country, workplace_label, workplace_code, exp_text, years_num, stage_label, stage_code, stage_badge


def calculate_match_details(cv_keywords: set, user_languages: set, job: dict):
    title = job.get("title", "")
    description = job.get("descriptionPlain", "")
    job_text = f"{title} {description}".lower()

    country, workplace_label, workplace_code, exp_text, years_num, stage_label, stage_code, stage_badge = parse_job_metadata(job)

    required_foreign, unsupported_langs = detect_language_requirements(job_text, user_languages)

    if not cv_keywords:
        return 0.0, [], country, workplace_label, workplace_code, exp_text, years_num, stage_label, stage_code, stage_badge, unsupported_langs

    matched = [kw for kw in cv_keywords if kw in job_text and len(kw) > 3]
    title_matches = [kw for kw in cv_keywords if kw in title.lower() and len(kw) > 3]

    score = (len(matched) / len(cv_keywords)) * 100
    if title_matches:
        score += len(title_matches) * 10

    if stage_code in ["entry", "intern"]:
        score += 15.0

    if unsupported_langs:
        score -= 40.0

    score = max(0.0, min(round(score, 1), 100.0))
    matched_tags = list(set(title_matches + matched))[:5]
    return score, matched_tags, country, workplace_label, workplace_code, exp_text, years_num, stage_label, stage_code, stage_badge, unsupported_langs


def build_dashboard_html(jobs_data: list, total_companies: int, total_jobs: int):
    jobs_json = json.dumps(jobs_data)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Unified Multi-ATS Startup Career Engine (Ashby + Greenhouse + Lever)</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-dark: #07090e;
            --card-bg: #111622;
            --card-border: #1d263b;
            --accent-green: #10b981;
            --accent-blue: #3b82f6;
            --accent-purple: #8b5cf6;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
        }}

        body {{
            background-color: var(--bg-dark);
            color: var(--text-main);
            min-height: 100vh;
            padding: 2rem;
            background-image: 
                radial-gradient(at 0% 0%, rgba(59, 130, 246, 0.12) 0px, transparent 50%),
                radial-gradient(at 100% 0%, rgba(16, 185, 129, 0.12) 0px, transparent 50%);
        }}

        .container {{
            max-width: 1440px;
            margin: 0 auto;
        }}

        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2rem;
            padding-bottom: 1.5rem;
            border-bottom: 1px solid var(--card-border);
        }}

        .logo-title h1 {{
            font-size: 2.2rem;
            font-weight: 800;
            background: linear-gradient(135deg, #60a5fa 0%, #34d399 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}

        .logo-title p {{
            color: var(--text-muted);
            font-size: 0.95rem;
            margin-top: 0.25rem;
        }}

        .view-tabs {{
            display: flex;
            gap: 1rem;
            margin-bottom: 1.5rem;
            border-bottom: 1px solid var(--card-border);
            padding-bottom: 0.5rem;
        }}

        .tab-btn {{
            background: transparent;
            border: none;
            color: var(--text-muted);
            font-size: 1rem;
            font-weight: 700;
            padding: 0.6rem 1.2rem;
            cursor: pointer;
            border-radius: 8px;
            transition: all 0.2s ease;
        }}

        .tab-btn.active {{
            background: rgba(59, 130, 246, 0.15);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.3);
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }}

        .stat-card {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 14px;
            padding: 1.25rem;
            backdrop-filter: blur(10px);
        }}

        .stat-value {{
            font-size: 1.9rem;
            font-weight: 800;
            color: var(--text-main);
        }}

        .stat-label {{
            font-size: 0.85rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-top: 0.25rem;
        }}

        .filter-panel {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 1.5rem;
            margin-bottom: 2rem;
            display: flex;
            flex-direction: column;
            gap: 1.25rem;
        }}

        .search-row {{
            display: flex;
            gap: 1rem;
            flex-wrap: wrap;
        }}

        .search-box {{
            flex: 1;
            min-width: 300px;
        }}

        .search-box input {{
            width: 100%;
            background: #090d14;
            border: 1px solid var(--card-border);
            color: white;
            padding: 0.85rem 1.2rem;
            border-radius: 10px;
            font-size: 0.95rem;
            outline: none;
            transition: all 0.2s ease;
        }}

        .search-box input:focus {{
            border-color: var(--accent-blue);
            box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
        }}

        .sort-select {{
            background: #090d14;
            border: 1px solid var(--card-border);
            color: var(--text-main);
            padding: 0.85rem 1.2rem;
            border-radius: 10px;
            font-size: 0.9rem;
            outline: none;
            cursor: pointer;
        }}

        .filter-group-title {{
            font-size: 0.8rem;
            font-weight: 700;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.06em;
            margin-bottom: 0.5rem;
        }}

        .filter-row {{
            display: flex;
            flex-direction: column;
            gap: 1.1rem;
        }}

        .tag-group {{
            display: flex;
            gap: 0.5rem;
            flex-wrap: wrap;
        }}

        .filter-chip {{
            background: #090d14;
            border: 1px solid var(--card-border);
            color: var(--text-muted);
            padding: 0.55rem 1rem;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            user-select: none;
            transition: all 0.2s ease;
        }}

        .filter-chip:hover {{
            border-color: var(--accent-blue);
            color: white;
        }}

        .filter-chip.active {{
            background: var(--accent-blue);
            color: white;
            border-color: var(--accent-blue);
            box-shadow: 0 2px 8px rgba(59, 130, 246, 0.4);
        }}

        .filter-chip.active-green {{
            background: var(--accent-green);
            color: #07090e;
            border-color: var(--accent-green);
            font-weight: 800;
            box-shadow: 0 2px 8px rgba(16, 185, 129, 0.4);
        }}

        .clear-btn {{
            background: transparent;
            border: 1px dashed var(--card-border);
            color: #ef4444;
            padding: 0.55rem 1rem;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
        }}

        .clear-btn:hover {{
            background: rgba(239, 68, 68, 0.15);
            border-color: #ef4444;
        }}

        .results-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
            color: var(--text-muted);
            font-size: 0.95rem;
        }}

        .jobs-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(390px, 1fr));
            gap: 1.5rem;
        }}

        .job-card {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            transition: all 0.25s ease;
            position: relative;
        }}

        .job-card:hover {{
            transform: translateY(-4px);
            border-color: #334155;
            box-shadow: 0 12px 24px -10px rgba(0, 0, 0, 0.5);
        }}

        .job-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 0.8rem;
        }}

        .company-name {{
            font-size: 0.85rem;
            font-weight: 700;
            color: #60a5fa;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }}

        .job-title {{
            font-size: 1.15rem;
            font-weight: 700;
            color: var(--text-main);
            margin-top: 0.3rem;
            line-height: 1.3;
        }}

        .match-badge {{
            padding: 0.35rem 0.75rem;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: 800;
            white-space: nowrap;
        }}

        .match-emerald {{
            background: rgba(16, 185, 129, 0.18);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.4);
        }}

        .match-blue {{
            background: rgba(59, 130, 246, 0.18);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.4);
        }}

        .match-purple {{
            background: rgba(139, 92, 246, 0.18);
            color: #c4b5fd;
            border: 1px solid rgba(139, 92, 246, 0.4);
        }}

        .meta-tags {{
            display: flex;
            gap: 0.5rem;
            flex-wrap: wrap;
            margin: 0.7rem 0;
        }}

        .tag {{
            background: #090d14;
            color: var(--text-muted);
            border: 1px solid var(--card-border);
            padding: 0.3rem 0.6rem;
            border-radius: 6px;
            font-size: 0.78rem;
        }}

        .ats-ashby {{
            background: rgba(139, 92, 246, 0.2);
            color: #c4b5fd;
            border: 1px solid rgba(139, 92, 246, 0.5);
            font-weight: 800;
        }}

        .ats-greenhouse {{
            background: rgba(16, 185, 129, 0.2);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.5);
            font-weight: 800;
        }}

        .ats-lever {{
            background: rgba(59, 130, 246, 0.2);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.5);
            font-weight: 800;
        }}

        .stage-intern-badge {{
            background: rgba(236, 72, 153, 0.2);
            color: #f472b6;
            border: 1px solid rgba(236, 72, 153, 0.5);
            font-weight: 700;
        }}

        .stage-entry-badge {{
            background: rgba(16, 185, 129, 0.2);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.5);
            font-weight: 700;
        }}

        .stage-mid-badge {{
            background: rgba(59, 130, 246, 0.2);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.4);
            font-weight: 700;
        }}

        .stage-senior-badge {{
            background: rgba(139, 92, 246, 0.2);
            color: #c4b5fd;
            border: 1px solid rgba(139, 92, 246, 0.4);
            font-weight: 700;
        }}

        .stage-exec-badge {{
            background: rgba(245, 158, 11, 0.2);
            color: #fbbf24;
            border: 1px solid rgba(245, 158, 11, 0.5);
            font-weight: 800;
        }}

        .lang-alert-badge {{
            background: rgba(239, 68, 68, 0.2);
            color: #f87171;
            border: 1px solid rgba(239, 68, 68, 0.5);
            font-weight: 700;
        }}

        .exp-badge {{
            background: rgba(245, 158, 11, 0.15);
            color: #fbbf24;
            border: 1px solid rgba(245, 158, 11, 0.4);
            font-weight: 700;
        }}

        .mode-remote-badge {{
            background: rgba(59, 130, 246, 0.2);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.5);
            font-weight: 700;
        }}

        .mode-hybrid-badge {{
            background: rgba(245, 158, 11, 0.2);
            color: #fbbf24;
            border: 1px solid rgba(245, 158, 11, 0.5);
            font-weight: 700;
        }}

        .mode-onsite-badge {{
            background: rgba(156, 163, 175, 0.15);
            color: #d1d5db;
            border: 1px solid rgba(156, 163, 175, 0.3);
            font-weight: 600;
        }}

        .status-applied-badge {{
            background: rgba(16, 185, 129, 0.25);
            color: #34d399;
            border: 1px solid #10b981;
            font-weight: 800;
        }}

        .status-saved-badge {{
            background: rgba(245, 158, 11, 0.25);
            color: #fbbf24;
            border: 1px solid #f59e0b;
            font-weight: 800;
        }}

        .skill-tag {{
            background: rgba(139, 92, 246, 0.15);
            color: #c4b5fd;
            border: 1px solid rgba(139, 92, 246, 0.3);
        }}

        .card-actions {{
            display: flex;
            gap: 0.5rem;
            margin-top: 1rem;
        }}

        .apply-btn {{
            flex: 1;
            text-align: center;
            background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
            color: white;
            text-decoration: none;
            font-weight: 700;
            padding: 0.8rem;
            border-radius: 10px;
            cursor: pointer;
            border: none;
            transition: all 0.2s ease;
        }}

        .apply-btn:hover {{
            background: linear-gradient(135deg, #1d4ed8 0%, #1e40af 100%);
            box-shadow: 0 4px 12px rgba(37, 99, 235, 0.4);
        }}

        .modal-overlay {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: rgba(7, 9, 14, 0.8);
            backdrop-filter: blur(8px);
            display: none;
            justify-content: center;
            align-items: center;
            z-index: 1000;
        }}

        .modal-card {{
            background: #111622;
            border: 1px solid #2d3748;
            border-radius: 20px;
            padding: 2rem;
            max-width: 500px;
            width: 90%;
            text-align: center;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.8);
        }}

        .modal-card h2 {{
            font-size: 1.4rem;
            margin-bottom: 0.5rem;
            color: white;
        }}

        .modal-card p {{
            color: var(--text-muted);
            margin-bottom: 1.5rem;
            font-size: 0.95rem;
        }}

        .modal-btn-grid {{
            display: flex;
            flex-direction: column;
            gap: 0.8rem;
        }}

        .modal-btn {{
            padding: 0.9rem;
            border-radius: 12px;
            font-weight: 700;
            font-size: 0.95rem;
            border: none;
            cursor: pointer;
            transition: all 0.2s ease;
        }}

        .btn-yes {{
            background: #10b981;
            color: #07090e;
        }}

        .btn-yes:hover {{
            background: #059669;
        }}

        .btn-save {{
            background: #f59e0b;
            color: #07090e;
        }}

        .btn-save:hover {{
            background: #d97706;
        }}

        .btn-archive {{
            background: #ef4444;
            color: white;
        }}

        .btn-archive:hover {{
            background: #dc2626;
        }}

        .btn-cancel {{
            background: #1f2937;
            color: var(--text-muted);
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo-title">
                <h1>Unified Multi-ATS Startup Career Engine</h1>
                <p>Scouring live jobs across {total_companies} company boards (Ashby + Greenhouse + Lever) matched against Grant Flores Akuoko's CV</p>
            </div>
        </header>

        <div class="view-tabs">
            <button class="tab-btn active" id="tabFeed" onclick="switchMainTab('feed')">🚀 Active Job Feed</button>
            <button class="tab-btn" id="tabApplied" onclick="switchMainTab('applied')">✅ Applied (<span id="appliedBadgeCount">0</span>)</button>
            <button class="tab-btn" id="tabSaved" onclick="switchMainTab('saved')">⏳ Apply Later / Saved (<span id="savedBadgeCount">0</span>)</button>
            <button class="tab-btn" id="tabArchived" onclick="switchMainTab('archived')">🗑️ Archived</button>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{total_jobs:,}</div>
                <div class="stat-label">Total Jobs Scanned</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{total_companies:,}</div>
                <div class="stat-label">Active Startup Boards</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="showingCount">0</div>
                <div class="stat-label">Matching Roles Filtered</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="entryCount">0</div>
                <div class="stat-label">🌱 Entry-Level Roles</div>
            </div>
        </div>

        <div class="filter-panel">
            <div class="search-row">
                <div class="search-box">
                    <input type="text" id="searchInput" placeholder="Search by title, company, skill, city, department (e.g. Monzo, Revolut, Griffin, Intern, SDR, London)..." oninput="renderFilteredJobs()">
                </div>
                <select id="sortSelect" class="sort-select" onchange="renderFilteredJobs()">
                    <option value="score-desc">Sort: Highest Match Score</option>
                    <option value="company-asc">Sort: Company Name (A-Z)</option>
                    <option value="title-asc">Sort: Job Title (A-Z)</option>
                </select>
            </div>

            <div class="filter-row">
                <div>
                    <div class="filter-group-title">ATS Job Board Provider</div>
                    <div class="tag-group" id="atsChips">
                        <span class="filter-chip active-green" data-ats="all" onclick="toggleAtsFilter('all', this)">🌍 All ATS Platforms (Ashby + Greenhouse + Lever)</span>
                        <span class="filter-chip" data-ats="Ashby" onclick="toggleAtsFilter('Ashby', this)">🟣 AshbyHQ</span>
                        <span class="filter-chip" data-ats="Greenhouse" onclick="toggleAtsFilter('Greenhouse', this)">🟢 Greenhouse</span>
                        <span class="filter-chip" data-ats="Lever" onclick="toggleAtsFilter('Lever', this)">🔵 Lever</span>
                    </div>
                </div>

                <div>
                    <div class="filter-group-title">Language Compatibility</div>
                    <div class="tag-group" id="langChips">
                        <span class="filter-chip active-green" data-lang="matched" onclick="toggleLangFilter('matched', this)">🇬🇧 Native English & 🇳🇱 Fluent Dutch Roles Only</span>
                        <span class="filter-chip" data-lang="all" onclick="toggleLangFilter('all', this)">Show All (Include German/French Required Roles)</span>
                    </div>
                </div>

                <div>
                    <div class="filter-group-title">Experience Level & Career Stage</div>
                    <div class="tag-group" id="stageChips">
                        <span class="filter-chip active-green" data-stage="entry" onclick="toggleStageFilter('entry', this)">🌱 Entry-Level / Junior (0 - 2 Yrs)</span>
                        <span class="filter-chip" data-stage="intern" onclick="toggleStageFilter('intern', this)">🎓 Internship / Placement / Student</span>
                        <span class="filter-chip" data-stage="mid" onclick="toggleStageFilter('mid', this)">💼 Mid-Level (3 - 5 Yrs)</span>
                        <span class="filter-chip" data-stage="senior" onclick="toggleStageFilter('senior', this)">👔 Senior Level (5 - 8 Yrs)</span>
                        <span class="filter-chip" data-stage="exec" onclick="toggleStageFilter('exec', this)">👑 Executive & Director (8+ Yrs)</span>
                        <span class="filter-chip" data-stage="all" onclick="toggleStageFilter('all', this)">🌍 All Career Stages</span>
                    </div>
                </div>

                <div>
                    <div class="filter-group-title">Workplace Mode</div>
                    <div class="tag-group" id="remoteChips">
                        <span class="filter-chip active" data-remote="all" onclick="toggleRemoteFilter('all', this)">All Modes</span>
                        <span class="filter-chip" data-remote="remote" onclick="toggleRemoteFilter('remote', this)">🌐 Fully Remote Only</span>
                        <span class="filter-chip" data-remote="hybrid" onclick="toggleRemoteFilter('hybrid', this)">🏢 Hybrid Only</span>
                        <span class="filter-chip" data-remote="onsite" onclick="toggleRemoteFilter('onsite', this)">🏛️ On-Site Only</span>
                    </div>
                </div>

                <div>
                    <div class="filter-group-title">Country / Region</div>
                    <div class="tag-group" id="countryChips">
                        <span class="filter-chip active" data-country="all" onclick="toggleCountryFilter('all', this)">All Countries</span>
                        <span class="filter-chip" data-country="United Kingdom" onclick="toggleCountryFilter('United Kingdom', this)">🇬🇧 United Kingdom</span>
                        <span class="filter-chip" data-country="United States" onclick="toggleCountryFilter('United States', this)">🇺🇸 United States</span>
                        <span class="filter-chip" data-country="Canada" onclick="toggleCountryFilter('Canada', this)">🇨🇦 Canada</span>
                        <span class="filter-chip" data-country="Germany" onclick="toggleCountryFilter('Germany', this)">🇩🇪 Germany</span>
                        <span class="filter-chip" data-country="France" onclick="toggleCountryFilter('France', this)">🇫🇷 France</span>
                        <span class="filter-chip" data-country="Netherlands" onclick="toggleCountryFilter('Netherlands', this)">🇳🇱 Netherlands</span>
                    </div>
                </div>

                <div>
                    <div class="filter-group-title">Minimum Match Percentage</div>
                    <div class="tag-group" id="scoreChips">
                        <span class="filter-chip active" data-score="0" onclick="toggleScoreFilter(0, this)">All Matches (0%+)</span>
                        <span class="filter-chip" data-score="35" onclick="toggleScoreFilter(35, this)">35%+ Match</span>
                        <span class="filter-chip" data-score="50" onclick="toggleScoreFilter(50, this)">50%+ Match</span>
                        <span class="filter-chip" data-score="70" onclick="toggleScoreFilter(70, this)">70%+ Match</span>
                        <button class="clear-btn" onclick="resetAllFilters()">Reset All Filters</button>
                    </div>
                </div>
            </div>
        </div>

        <div class="results-bar">
            <span id="resultsCountText">Showing matching roles...</span>
        </div>

        <div class="jobs-grid" id="jobsGrid"></div>
    </div>

    <div class="modal-overlay" id="applyModal">
        <div class="modal-card">
            <h2 id="modalTitle">Application Prompt</h2>
            <p id="modalCompany">Did you complete your application for this position?</p>
            <div class="modal-btn-grid">
                <button class="modal-btn btn-yes" onclick="setJobStatus('applied')">✅ Yes, I Submitted My Application</button>
                <button class="modal-btn btn-save" onclick="setJobStatus('saved')">⏳ Plan to Apply Later (Save Role)</button>
                <button class="modal-btn btn-archive" onclick="setJobStatus('archived')">❌ Not Interested (Archive)</button>
                <button class="modal-btn btn-cancel" onclick="closeModal()">Dismiss</button>
            </div>
        </div>
    </div>

    <script>
        const allJobs = {jobs_json};
        let currentTab = 'feed';
        let activeAts = 'all';
        let activeLang = 'matched';
        let activeStage = 'entry';
        let activeCountry = 'all';
        let activeRemote = 'all';
        let activeDept = 'all';
        let minScore = 0;
        let activeJobId = null;

        function getJobStatusMap() {{
            const stored = localStorage.getItem('ashby_job_tracker_status');
            return stored ? JSON.parse(stored) : {{}};
        }}

        function saveJobStatusMap(map) {{
            localStorage.setItem('ashby_job_tracker_status', JSON.stringify(map));
        }}

        function switchMainTab(tabName) {{
            currentTab = tabName;
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            if (tabName === 'feed') document.getElementById('tabFeed').classList.add('active');
            if (tabName === 'applied') document.getElementById('tabApplied').classList.add('active');
            if (tabName === 'saved') document.getElementById('tabSaved').classList.add('active');
            if (tabName === 'archived') document.getElementById('tabArchived').classList.add('active');
            renderFilteredJobs();
        }}

        function openApplyJob(jobId, url) {{
            activeJobId = jobId;
            window.open(url, '_blank');
            const job = allJobs.find(j => (j.company + '_' + j.title) === jobId);
            if (job) {{
                document.getElementById('modalTitle').innerText = job.title;
                document.getElementById('modalCompany').innerText = `Did you submit your application to ${{job.company}}?`;
                document.getElementById('applyModal').style.display = 'flex';
            }}
        }}

        function closeModal() {{
            document.getElementById('applyModal').style.display = 'none';
            activeJobId = null;
        }}

        function setJobStatus(status) {{
            if (!activeJobId) return;
            const map = getJobStatusMap();
            map[activeJobId] = status;
            saveJobStatusMap(map);
            closeModal();
            renderFilteredJobs();
        }}

        function toggleAtsFilter(atsMode, el) {{
            document.querySelectorAll('#atsChips .filter-chip').forEach(c => c.classList.remove('active', 'active-green'));
            if (atsMode === 'all') el.classList.add('active-green');
            else el.classList.add('active');
            activeAts = atsMode;
            renderFilteredJobs();
        }}

        function toggleLangFilter(langMode, el) {{
            document.querySelectorAll('#langChips .filter-chip').forEach(c => c.classList.remove('active', 'active-green'));
            if (langMode === 'matched') el.classList.add('active-green');
            else el.classList.add('active');
            activeLang = langMode;
            renderFilteredJobs();
        }}

        function toggleStageFilter(stage, el) {{
            document.querySelectorAll('#stageChips .filter-chip').forEach(c => c.classList.remove('active', 'active-green'));
            if (stage === 'entry' || stage === 'intern') el.classList.add('active-green');
            else el.classList.add('active');
            activeStage = stage;
            renderFilteredJobs();
        }}

        function toggleCountryFilter(country, el) {{
            document.querySelectorAll('#countryChips .filter-chip').forEach(c => c.classList.remove('active'));
            el.classList.add('active');
            activeCountry = country;
            renderFilteredJobs();
        }}

        function toggleRemoteFilter(remoteMode, el) {{
            document.querySelectorAll('#remoteChips .filter-chip').forEach(c => c.classList.remove('active'));
            el.classList.add('active');
            activeRemote = remoteMode;
            renderFilteredJobs();
        }}

        function toggleScoreFilter(score, el) {{
            document.querySelectorAll('#scoreChips .filter-chip').forEach(c => c.classList.remove('active'));
            el.classList.add('active');
            minScore = score;
            renderFilteredJobs();
        }}

        function resetAllFilters() {{
            document.getElementById('searchInput').value = '';
            document.getElementById('sortSelect').value = 'score-desc';
            activeAts = 'all';
            activeLang = 'matched';
            activeStage = 'entry';
            activeCountry = 'all';
            activeRemote = 'all';
            minScore = 0;

            document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active', 'active-green'));
            document.querySelector('#atsChips [data-ats="all"]').classList.add('active-green');
            document.querySelector('#langChips [data-lang="matched"]').classList.add('active-green');
            document.querySelector('#stageChips [data-stage="entry"]').classList.add('active-green');
            document.querySelector('#countryChips [data-country="all"]').classList.add('active');
            document.querySelector('#remoteChips [data-remote="all"]').classList.add('active');
            document.querySelector('#scoreChips [data-score="0"]').classList.add('active');

            renderFilteredJobs();
        }}

        function renderFilteredJobs() {{
            const search = document.getElementById('searchInput').value.toLowerCase().trim();
            const sortMode = document.getElementById('sortSelect').value;
            const statusMap = getJobStatusMap();

            let appliedCount = 0;
            let savedCount = 0;

            Object.values(statusMap).forEach(val => {{
                if (val === 'applied') appliedCount++;
                if (val === 'saved') savedCount++;
            }});

            document.getElementById('appliedBadgeCount').innerText = appliedCount;
            document.getElementById('savedBadgeCount').innerText = savedCount;

            let filtered = allJobs.filter(job => {{
                const jobId = job.company + '_' + job.title;
                const status = statusMap[jobId] || 'feed';

                if (currentTab === 'feed' && status !== 'feed') return false;
                if (currentTab === 'applied' && status !== 'applied') return false;
                if (currentTab === 'saved' && status !== 'saved') return false;
                if (currentTab === 'archived' && status !== 'archived') return false;

                if (activeAts !== 'all' && job.ats !== activeAts) return false;

                if (activeLang === 'matched' && job.unsupported_langs && job.unsupported_langs.length > 0) return false;

                if (job.score < minScore) return false;

                if (activeStage !== 'all' && job.stage_code !== activeStage) return false;

                if (activeCountry !== 'all' && job.country !== activeCountry) return false;

                if (activeRemote === 'remote' && job.workplace_code !== 'remote') return false;
                if (activeRemote === 'hybrid' && job.workplace_code !== 'hybrid') return false;
                if (activeRemote === 'onsite' && job.workplace_code !== 'onsite') return false;

                const textMatches = !search || 
                    job.title.toLowerCase().includes(search) ||
                    job.company.toLowerCase().includes(search) ||
                    job.location.toLowerCase().includes(search) ||
                    job.department.toLowerCase().includes(search) ||
                    job.stage_label.toLowerCase().includes(search) ||
                    job.workplace_label.toLowerCase().includes(search) ||
                    job.exp_text.toLowerCase().includes(search) ||
                    job.matched_tags.some(t => t.toLowerCase().includes(search));

                if (!textMatches) return false;

                return true;
            }});

            if (sortMode === 'score-desc') {{
                filtered.sort((a, b) => b.score - a.score);
            }} else if (sortMode === 'company-asc') {{
                filtered.sort((a, b) => a.company.localeCompare(b.company));
            }} else if (sortMode === 'title-asc') {{
                filtered.sort((a, b) => a.title.localeCompare(b.title));
            }}

            document.getElementById('showingCount').innerText = filtered.length.toLocaleString();
            document.getElementById('entryCount').innerText = allJobs.filter(j => j.stage_code === 'entry').length.toLocaleString();
            document.getElementById('resultsCountText').innerText = `Displaying ${{filtered.length.toLocaleString()}} positions in ${{currentTab.toUpperCase()}} tab`;

            const grid = document.getElementById('jobsGrid');
            grid.innerHTML = '';

            filtered.slice(0, 200).forEach(job => {{
                const jobId = job.company + '_' + job.title;
                const status = statusMap[jobId] || 'feed';
                const card = document.createElement('div');
                card.className = 'job-card';

                let matchClass = 'match-purple';
                if (job.score >= 85) matchClass = 'match-emerald';
                else if (job.score >= 70) matchClass = 'match-blue';

                let atsBadgeClass = 'ats-ashby';
                if (job.ats === 'Greenhouse') atsBadgeClass = 'ats-greenhouse';
                if (job.ats === 'Lever') atsBadgeClass = 'ats-lever';

                let stageBadgeClass = 'stage-mid-badge';
                if (job.stage_code === 'intern') stageBadgeClass = 'stage-intern-badge';
                if (job.stage_code === 'entry') stageBadgeClass = 'stage-entry-badge';
                if (job.stage_code === 'senior') stageBadgeClass = 'stage-senior-badge';
                if (job.stage_code === 'exec') stageBadgeClass = 'stage-exec-badge';

                const atsBadge = `<span class="tag ${{atsBadgeClass}}">${{job.ats}}</span>`;
                const stageBadge = `<span class="tag ${{stageBadgeClass}}">${{job.stage_badge}}</span>`;

                let modeBadge = `<span class="tag mode-onsite-badge">🏛️ On-Site</span>`;
                if (job.workplace_code === 'remote') modeBadge = `<span class="tag mode-remote-badge">🌐 Fully Remote</span>`;
                if (job.workplace_code === 'hybrid') modeBadge = `<span class="tag mode-hybrid-badge">🏢 Hybrid</span>`;

                const expBadge = `<span class="tag exp-badge">⏳ ${{job.exp_text}}</span>`;

                let langBadge = '';
                if (job.unsupported_langs && job.unsupported_langs.length > 0) {{
                    langBadge = `<span class="tag lang-alert-badge">⚠️ Requires ${{job.unsupported_langs.join(', ')}}</span>`;
                }}

                let statusBadge = '';
                if (status === 'applied') statusBadge = `<span class="tag status-applied-badge">✅ Applied</span>`;
                if (status === 'saved') statusBadge = `<span class="tag status-saved-badge">⏳ Saved for Later</span>`;

                const skillBadges = job.matched_tags.map(t => `<span class="tag skill-tag">${{t}}</span>`).join('');

                card.innerHTML = `
                    <div>
                        <div class="job-header">
                            <div>
                                <div class="company-name">${{job.company}}</div>
                                <div class="job-title">${{job.title}}</div>
                            </div>
                            <div class="match-badge ${{matchClass}}">${{job.score}}% Match</div>
                        </div>
                        <div class="meta-tags">
                            ${{statusBadge}}
                            ${{atsBadge}}
                            ${{stageBadge}}
                            ${{modeBadge}}
                            ${{expBadge}}
                            ${{langBadge}}
                            <span class="tag">📍 ${{job.location}}</span>
                        </div>
                        <div class="meta-tags">
                            ${{skillBadges}}
                        </div>
                    </div>
                    <div class="card-actions">
                        <button onclick="openApplyJob('${{jobId}}', '${{job.url}}')" class="apply-btn">Apply Now →</button>
                    </div>
                `;
                grid.appendChild(card);
            }});
        }}

        renderFilteredJobs();
    </script>
</body>
</html>"""
    return html_content


def main():
    cv_path = Path("cv.txt")
    cv_text = cv_path.read_text(encoding="utf-8") if cv_path.exists() else ""
    cv_keywords = extract_keywords(cv_text)

    user_languages = {"english"}
    if "dutch" in cv_text.lower():
        user_languages.add("dutch")

    print("Discovering active company boards across Ashby, Greenhouse & Lever...")
    ashby_slugs, gh_slugs, lever_slugs = asyncio.run(discover_all_ats_slugs())
    total_boards = len(ashby_slugs) + len(gh_slugs) + len(lever_slugs)
    print(f"Discovered {total_boards} total company boards ({len(ashby_slugs)} Ashby, {len(gh_slugs)} Greenhouse, {len(lever_slugs)} Lever).")

    print(f"Fetching open positions from all {total_boards} boards in parallel...")
    all_jobs = asyncio.run(fetch_all_ats_jobs(ashby_slugs, gh_slugs, lever_slugs))
    print(f"Fetched {len(all_jobs)} open positions in total.\n")

    jobs_data = []
    for job in all_jobs:
        score, tags, country, workplace_label, workplace_code, exp_text, years_num, stage_label, stage_code, stage_badge, unsupported_langs = calculate_match_details(cv_keywords, user_languages, job)
        jobs_data.append({
            "company": job.get("company", "").capitalize(),
            "title": job.get("title"),
            "department": job.get("department") or "General",
            "location": str(job.get("location") or "Unspecified"),
            "url": job.get("jobUrl"),
            "ats": job.get("ats"),
            "score": score,
            "matched_tags": tags,
            "country": country,
            "workplace_label": workplace_label,
            "workplace_code": workplace_code,
            "exp_text": exp_text,
            "years_num": years_num,
            "stage_label": stage_label,
            "stage_code": stage_code,
            "stage_badge": stage_badge,
            "unsupported_langs": unsupported_langs
        })

    jobs_data.sort(key=lambda x: x["score"], reverse=True)

    output_path = Path("job_dashboard.html")
    html = build_dashboard_html(jobs_data, total_boards, len(all_jobs))
    output_path.write_text(html, encoding="utf-8")

    print("==========================================================")
    print(" 🚀 UNIFIED MULTI-ATS STARTUP ENGINE: job_dashboard.html  ")
    print("==========================================================\n")
    print(f"Opening dashboard in your web browser...")
    webbrowser.open(f"file://{output_path.resolve()}")


if __name__ == "__main__":
    main()
