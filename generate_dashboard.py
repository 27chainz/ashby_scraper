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

    return country, workplace_label, workplace_code, exp_text, years_num


def calculate_match_details(cv_keywords: set, job: dict):
    title = job.get("title", "")
    description = job.get("descriptionPlain", "")
    job_text = f"{title} {description}".lower()
    title_lower = title.lower()

    country, workplace_label, workplace_code, exp_text, years_num = parse_job_metadata(job)

    senior_patterns = [
        r"\bvp\b", r"\bvice president\b", r"\bdirector\b", r"\bhead of\b", r"\bchief\b",
        r"\bexecutive\b", r"\bprincipal\b", r"\bpartner\b", r"\bsenior\b", r"\bsr\.\b", r"\bsr\b", r"\blead\b"
    ]
    is_senior = any(re.search(pat, title_lower) for pat in senior_patterns) or (years_num is not None and years_num >= 5)

    student_patterns = [
        r"\bintern\b", r"\binternship\b", r"\bplacement\b", r"\bco-op\b", r"\bcoop\b",
        r"\bworking student\b", r"\bstudent\b", r"\bapprentice\b", r"\bapprenticeship\b"
    ]
    has_student_term = any(re.search(pat, title_lower) for pat in student_patterns)
    is_student_intern = has_student_term and not is_senior and "internal" not in title_lower and "international" not in title_lower

    early_patterns = [
        r"\bbdr\b", r"\bsdr\b", r"\bbusiness development representative\b", r"\bsales development representative\b",
        r"\bjunior\b", r"\bassociate\b", r"\banalyst\b", r"\bgraduate\b", r"\bnew grad\b", r"\btrainee\b", r"\bentry level\b", r"\bassistant\b"
    ]
    has_early_term = any(re.search(pat, title_lower) for pat in early_patterns)
    is_early_career = (is_student_intern or (has_early_term and not is_senior) or (years_num is not None and years_num <= 2)) and not is_senior

    if not cv_keywords:
        return 0.0, [], False, False, country, workplace_label, workplace_code, exp_text, years_num

    matched = [kw for kw in cv_keywords if kw in job_text and len(kw) > 3]
    title_matches = [kw for kw in cv_keywords if kw in title_lower and len(kw) > 3]

    score = (len(matched) / len(cv_keywords)) * 100
    if title_matches:
        score += len(title_matches) * 10

    if is_early_career:
        score += 15.0

    score = min(round(score, 1), 100.0)
    matched_tags = list(set(title_matches + matched))[:5]
    return score, matched_tags, is_early_career, is_student_intern, country, workplace_label, workplace_code, exp_text, years_num


def build_dashboard_html(jobs_data: list, total_companies: int, total_jobs: int):
    jobs_json = json.dumps(jobs_data)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AshbyHQ Precision Career Engine</title>
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

        .student-badge {{
            background: rgba(236, 72, 153, 0.2);
            color: #f472b6;
            border: 1px solid rgba(236, 72, 153, 0.5);
            font-weight: 700;
        }}

        .early-badge {{
            background: rgba(16, 185, 129, 0.2);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.5);
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
                <h1>AshbyHQ Precision Career Engine</h1>
                <p>Scouring live jobs across {total_companies} company boards matched against Grant Flores Akuoko's CV</p>
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
                <div class="stat-value" id="appliedCount">0</div>
                <div class="stat-label">Applications Submitted</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="showingCount">0</div>
                <div class="stat-label">Matching Roles Filtered</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="studentCount">0</div>
                <div class="stat-label">🎓 Verified Intern & Placement Roles</div>
            </div>
        </div>

        <div class="filter-panel">
            <div class="search-row">
                <div class="search-box">
                    <input type="text" id="searchInput" placeholder="Search by role, company, skill (e.g. Intern, Placement, SDR, BDR, London)..." oninput="renderFilteredJobs()">
                </div>
                <select id="sortSelect" class="sort-select" onchange="renderFilteredJobs()">
                    <option value="score-desc">Sort: Highest Match Score</option>
                    <option value="company-asc">Sort: Company Name (A-Z)</option>
                    <option value="title-asc">Sort: Job Title (A-Z)</option>
                </select>
            </div>

            <div class="filter-row">
                <div>
                    <div class="filter-group-title">Experience Level & Career Stage</div>
                    <div class="tag-group" id="expChips">
                        <span class="filter-chip active-green" data-exp="early" onclick="toggleExpFilter('early', this)">🌱 All Early Career & Entry-Level (0 - 2 Yrs)</span>
                        <span class="filter-chip" data-exp="student" onclick="toggleExpFilter('student', this)">🎓 Verified Students, Internships & Placements Only</span>
                        <span class="filter-chip" data-exp="grad" onclick="toggleExpFilter('grad', this)">💼 Graduates & Entry-Level Roles (SDR / BDR / Analyst)</span>
                        <span class="filter-chip" data-exp="all" onclick="toggleExpFilter('all', this)">All Experience Levels</span>
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
                    <div class="filter-group-title">Department / Sector</div>
                    <div class="tag-group" id="deptChips">
                        <span class="filter-chip active" data-dept="all" onclick="toggleDeptFilter('all', this)">All Departments</span>
                        <span class="filter-chip" data-dept="sales" onclick="toggleDeptFilter('sales', this)">💼 Sales & SDR / BDR</span>
                        <span class="filter-chip" data-dept="bizdev" onclick="toggleDeptFilter('bizdev', this)">🤝 Business Development</span>
                        <span class="filter-chip" data-dept="finance" onclick="toggleDeptFilter('finance', this)">📊 Finance & Analytics</span>
                        <span class="filter-chip" data-dept="product" onclick="toggleDeptFilter('product', this)">🎯 Product & Operations</span>
                        <span class="filter-chip" data-dept="marketing" onclick="toggleDeptFilter('marketing', this)">🚀 Marketing & Growth</span>
                    </div>
                </div>

                <div>
                    <div class="filter-group-title">Minimum Match Percentage</div>
                    <div class="tag-group" id="scoreChips">
                        <span class="filter-chip active" data-score="50" onclick="toggleScoreFilter(50, this)">All Matches (50%+)</span>
                        <span class="filter-chip" data-score="65" onclick="toggleScoreFilter(65, this)">65%+ Match</span>
                        <span class="filter-chip" data-score="75" onclick="toggleScoreFilter(75, this)">75%+ Match</span>
                        <span class="filter-chip" data-score="85" onclick="toggleScoreFilter(85, this)">85%+ Match</span>
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
        let activeExp = 'early';
        let activeCountry = 'all';
        let activeRemote = 'all';
        let activeDept = 'all';
        let minScore = 50;
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

        function toggleExpFilter(expMode, el) {{
            document.querySelectorAll('#expChips .filter-chip').forEach(c => c.classList.remove('active', 'active-green'));
            if (expMode === 'early' || expMode === 'student') el.classList.add('active-green');
            else el.classList.add('active');
            activeExp = expMode;
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

        function toggleDeptFilter(dept, el) {{
            document.querySelectorAll('#deptChips .filter-chip').forEach(c => c.classList.remove('active'));
            el.classList.add('active');
            activeDept = dept;
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
            activeExp = 'early';
            activeCountry = 'all';
            activeRemote = 'all';
            activeDept = 'all';
            minScore = 50;

            document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active', 'active-green'));
            document.querySelector('#expChips [data-exp="early"]').classList.add('active-green');
            document.querySelector('#countryChips [data-country="all"]').classList.add('active');
            document.querySelector('#remoteChips [data-remote="all"]').classList.add('active');
            document.querySelector('#deptChips [data-dept="all"]').classList.add('active');
            document.querySelector('#scoreChips [data-score="50"]').classList.add('active');

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
            document.getElementById('appliedCount').innerText = appliedCount;

            let filtered = allJobs.filter(job => {{
                const jobId = job.company + '_' + job.title;
                const status = statusMap[jobId] || 'feed';

                if (currentTab === 'feed' && status !== 'feed') return false;
                if (currentTab === 'applied' && status !== 'applied') return false;
                if (currentTab === 'saved' && status !== 'saved') return false;
                if (currentTab === 'archived' && status !== 'archived') return false;

                if (job.score < minScore) return false;

                if (activeExp === 'early' && !job.is_early_career) return false;
                if (activeExp === 'student' && !job.is_student_intern) return false;
                if (activeExp === 'grad' && (!job.is_early_career || job.is_student_intern)) return false;

                if (activeCountry !== 'all' && job.country !== activeCountry) return false;

                if (activeRemote === 'remote' && job.workplace_code !== 'remote') return false;
                if (activeRemote === 'hybrid' && job.workplace_code !== 'hybrid') return false;
                if (activeRemote === 'onsite' && job.workplace_code !== 'onsite') return false;

                const textMatches = !search || 
                    job.title.toLowerCase().includes(search) ||
                    job.company.toLowerCase().includes(search) ||
                    job.location.toLowerCase().includes(search) ||
                    job.department.toLowerCase().includes(search) ||
                    job.matched_tags.some(t => t.toLowerCase().includes(search));

                if (!textMatches) return false;

                const deptLower = job.department.toLowerCase();
                const titleLower = job.title.toLowerCase();
                if (activeDept === 'sales' && !(deptLower.includes('sales') || titleLower.includes('sales') || titleLower.includes('sdr') || titleLower.includes('bdr') || titleLower.includes('account executive'))) return false;
                if (activeDept === 'bizdev' && !(deptLower.includes('business dev') || titleLower.includes('business dev') || titleLower.includes('bdr') || titleLower.includes('partnership'))) return false;
                if (activeDept === 'finance' && !(deptLower.includes('finance') || titleLower.includes('finance') || titleLower.includes('accounting') || titleLower.includes('analytic'))) return false;
                if (activeDept === 'product' && !(deptLower.includes('product') || titleLower.includes('product') || titleLower.includes('strategy') || titleLower.includes('operation'))) return false;
                if (activeDept === 'marketing' && !(deptLower.includes('market') || titleLower.includes('market') || titleLower.includes('growth'))) return false;

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
            document.getElementById('studentCount').innerText = allJobs.filter(j => j.is_student_intern).length.toLocaleString();
            document.getElementById('resultsCountText').innerText = `Displaying ${{filtered.length.toLocaleString()}} positions in ${{currentTab.toUpperCase()}} tab`;

            const grid = document.getElementById('jobsGrid');
            grid.innerHTML = '';

            filtered.slice(0, 150).forEach(job => {{
                const jobId = job.company + '_' + job.title;
                const status = statusMap[jobId] || 'feed';
                const card = document.createElement('div');
                card.className = 'job-card';

                let matchClass = 'match-purple';
                if (job.score >= 85) matchClass = 'match-emerald';
                else if (job.score >= 70) matchClass = 'match-blue';

                const studentBadge = job.is_student_intern ? `<span class="tag student-badge">🎓 Student / Intern / Placement</span>` : '';
                const levelBadge = (!job.is_student_intern && job.is_early_career) ? `<span class="tag early-badge">🌱 Entry Level / Grad</span>` : '';

                let modeBadge = `<span class="tag mode-onsite-badge">🏛️ On-Site</span>`;
                if (job.workplace_code === 'remote') modeBadge = `<span class="tag mode-remote-badge">🌐 Fully Remote</span>`;
                if (job.workplace_code === 'hybrid') modeBadge = `<span class="tag mode-hybrid-badge">🏢 Hybrid</span>`;

                const expBadge = `<span class="tag exp-badge">⏳ ${{job.exp_text}}</span>`;

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
                            ${{studentBadge}}
                            ${{levelBadge}}
                            ${{modeBadge}}
                            ${{expBadge}}
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

    print("Discovering active AshbyHQ company boards...")
    slugs = asyncio.run(discover_company_slugs())
    print(f"Discovered {len(slugs)} active companies on AshbyHQ.")

    print(f"Fetching open positions from all {len(slugs)} boards...")
    all_jobs = asyncio.run(fetch_all_jobs_chunked(slugs))
    print(f"Fetched {len(all_jobs)} open positions in total.\n")

    jobs_data = []
    for job in all_jobs:
        score, tags, is_early, is_student, country, workplace_label, workplace_code, exp_text, years_num = calculate_match_details(cv_keywords, job)
        if score >= 50.0:
            jobs_data.append({
                "company": job.get("company", "").capitalize(),
                "title": job.get("title"),
                "department": job.get("department") or "General",
                "location": str(job.get("location") or "Unspecified"),
                "url": job.get("jobUrl"),
                "score": score,
                "matched_tags": tags,
                "is_early_career": is_early,
                "is_student_intern": is_student,
                "country": country,
                "workplace_label": workplace_label,
                "workplace_code": workplace_code,
                "exp_text": exp_text,
                "years_num": years_num
            })

    jobs_data.sort(key=lambda x: x["score"], reverse=True)

    output_path = Path("job_dashboard.html")
    html = build_dashboard_html(jobs_data, len(slugs), len(all_jobs))
    output_path.write_text(html, encoding="utf-8")

    print("==========================================================")
    print(" 🚀 PRECISION CLASSIFIED DASHBOARD: job_dashboard.html    ")
    print("==========================================================\n")
    print(f"Opening dashboard in your web browser...")
    webbrowser.open(f"file://{output_path.resolve()}")


if __name__ == "__main__":
    main()
