import asyncio
from pathlib import Path
from fetcher import fetch_all_jobs, COMPANY_SLUGS, extract_keywords


def analyze_cv(cv_path: str = "cv.txt"):
    path = Path(cv_path)
    if not path.exists():
        return "", set(), {}

    text = path.read_text(encoding="utf-8")
    keywords = extract_keywords(text)

    skill_categories = {
        "Sales & Commercial": ["sales", "pitching", "outreach", "client", "commercial", "account", "revenue", "gtm"],
        "Data & Analytics": ["python", "excel", "data", "analytics", "sql", "reporting", "modelling", "econometrics"],
        "Finance & Banking": ["reconciliation", "financial", "banking", "invoice", "audit", "compliance", "credit"],
        "AI & Tools": ["claude", "copilot", "cursor", "ai", "chatgpt"],
        "Languages": ["dutch", "english"]
    }

    detected_skills = {}
    lower_text = text.lower()
    for category, terms in skill_categories.items():
        found = [term for term in terms if term in lower_text]
        if found:
            detected_skills[category] = found

    return text, keywords, detected_skills


def evaluate_job(job: dict, cv_keywords: set, detected_skills: dict):
    title = job.get("title", "").lower()
    description = job.get("descriptionPlain", "").lower()
    job_text = f"{title} {description}"

    reasons = []
    score = 0.0

    title_matches = [kw for kw in cv_keywords if kw in title and len(kw) > 3]
    if title_matches:
        score += len(title_matches) * 15
        reasons.append(f"Title matches: {', '.join(title_matches[:3])}")

    for category, terms in detected_skills.items():
        matches = [t for t in terms if t in job_text]
        if matches:
            score += len(matches) * 5
            reasons.append(f"{category}: matches '{', '.join(matches[:3])}'")

    if "dutch" in job_text and "dutch" in str(detected_skills.get("Languages", [])):
        score += 30
        reasons.append("Requires Dutch fluency (matches your native/fluent Dutch skill)")

    return round(score, 1), reasons


def generate_job_recommendations():
    cv_text, cv_keywords, detected_skills = analyze_cv("cv.txt")
    if not cv_text:
        print("Error: cv.txt file not found!")
        return

    print("Analyzing CV for Grant Flores Akuoko...")
    print("Fetching live jobs from AshbyHQ...\n")

    jobs = asyncio.run(fetch_all_jobs(COMPANY_SLUGS))

    recommendations = []
    for job in jobs:
        score, reasons = evaluate_job(job, cv_keywords, detected_skills)
        if score > 20:
            recommendations.append({
                "company": job.get("company", "").capitalize(),
                "title": job.get("title"),
                "department": job.get("department") or "General",
                "location": job.get("location") or "Remote / Unspecified",
                "url": job.get("jobUrl"),
                "score": score,
                "reasons": reasons
            })

    recommendations.sort(key=lambda x: x["score"], reverse=True)

    print("==========================================================")
    print("       🎯 TOP RECOMMENDED JOBS BASED ON YOUR CV 🎯       ")
    print("==========================================================\n")

    for index, item in enumerate(recommendations[:10], start=1):
        print(f"{index}. [{item['score']} pts] {item['company']} - {item['title']}")
        print(f"   Location: {item['location']} | Department: {item['department']}")
        print("   Why it fits your CV:")
        for reason in item['reasons'][:3]:
            print(f"    • {reason}")
        print(f"   Apply Link: {item['url']}\n")


if __name__ == "__main__":
    generate_job_recommendations()
