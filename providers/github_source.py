"""GitHub provider (Depth 2). Free; GITHUB_TOKEN strongly recommended.

Covers: identity resolution (email -> login), public profile, byte-weighted
languages via /languages (fetched in parallel), contribution activity via
GraphQL (token only), and derived ICP basics (seniority, role hint, OSS activity).
"""
from __future__ import annotations
import concurrent.futures
import os
import re
import requests
from datetime import datetime, timezone

from .envelope import field, UA

REST = "https://api.github.com"
GQL = "https://api.github.com/graphql"

# Minimum match confidence to resolve a candidate into the profile.
# Below this threshold the candidate is recorded but not merged — avoids
# publishing a full profile for a low-confidence email-local-part guess.
MIN_RESOLVE_CONFIDENCE = 0.5


def _headers() -> dict[str, str]:
    h = {**UA, "Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _user(login: str) -> tuple[dict | None, str | None]:
    try:
        r = requests.get(f"{REST}/users/{login}", headers=_headers(), timeout=10)
    except Exception:
        return None, None
    if r.status_code == 403:
        return None, "rate_limited"
    if r.status_code != 200:
        return None, None
    try:
        return r.json(), None
    except Exception:
        return None, None


def _fetch_repo_langs(repo: dict) -> dict[str, int]:
    """Fetch byte-weighted language breakdown for one repo (called in parallel)."""
    if not repo.get("languages_url"):
        lang = repo.get("language")
        return {lang: 1} if lang else {}
    try:
        lr = requests.get(repo["languages_url"], headers=_headers(), timeout=8)
        if lr.status_code == 200:
            return lr.json()
    except Exception:
        pass
    lang = repo.get("language")
    return {lang: 1} if lang else {}


def _repo_languages(login: str, max_repos: int = 25) -> tuple[list[str], int, list[str]]:
    """Byte-weighted language tally across recent owned, non-fork repos.

    Language breakdowns for the first 12 repos are fetched in parallel.
    Repos beyond the first 12 count toward public_repo_count only — they
    do not contribute to language ranking, avoiding byte-vs-unit count mixing.
    """
    try:
        r = requests.get(
            f"{REST}/users/{login}/repos", headers=_headers(), timeout=10,
            params={"sort": "pushed", "per_page": max_repos, "type": "owner"})
    except Exception:
        return [], 0, []
    if r.status_code != 200:
        return [], 0, []
    try:
        repos = r.json()
    except Exception:
        return [], 0, []

    non_fork = [repo for repo in repos if not repo.get("fork")]
    topics: dict[str, int] = {}
    for repo in non_fork:
        for t in (repo.get("topics") or []):
            topics[t] = topics.get(t, 0) + 1

    lang_bytes: dict[str, int] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for lang_map in ex.map(_fetch_repo_langs, non_fork[:12]):
            for lang, b in lang_map.items():
                lang_bytes[lang] = lang_bytes.get(lang, 0) + b

    ranked_langs = sorted(lang_bytes, key=lambda k: lang_bytes[k], reverse=True)
    ranked_topics = sorted(topics, key=lambda k: topics[k], reverse=True)[:10]
    return ranked_langs, len(non_fork), ranked_topics


def _contribution_activity(login: str) -> int | None:
    """Total contributions in the last year via GraphQL. Requires a token."""
    if not os.environ.get("GITHUB_TOKEN"):
        return None
    q = """
    query($login:String!){
      user(login:$login){
        contributionsCollection{ contributionCalendar{ totalContributions } }
      }
    }"""
    try:
        r = requests.post(GQL, headers=_headers(),
                          json={"query": q, "variables": {"login": login}},
                          timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        return (data.get("data", {}).get("user", {})
                .get("contributionsCollection", {})
                .get("contributionCalendar", {})
                .get("totalContributions"))
    except Exception:
        return None


# ----- derivations ---------------------------------------------------------
def _derive_oss(repo_count: int, contributions: int | None) -> tuple[str, float]:
    if contributions is not None:
        if contributions == 0:
            return "None", 0.6
        if contributions < 100:
            return "Minimal", 0.65
        if contributions < 500:
            return "Moderate", 0.7
        return "Active", 0.75
    if repo_count == 0:
        return "None", 0.5
    if repo_count < 5:
        return "Minimal", 0.55
    if repo_count < 20:
        return "Moderate", 0.6
    return "Active", 0.65


def _derive_seniority(created_at: str | None) -> tuple[str | None, float]:
    """Rough bracket based on GitHub account age.

    Confidence is kept low (≤0.25) because account age is a weak proxy —
    a hobbyist with a 10-year-old account and a 10-year veteran look identical.
    Labels are intentionally non-committal; treat as a coarse hint only.
    """
    if not created_at:
        return None, 0.0
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except Exception:
        return None, 0.0
    years = (datetime.now(timezone.utc) - created).days / 365.25
    if years < 3:
        return "Early Career", 0.25
    if years < 8:
        return "Mid-Career", 0.25
    return "Established", 0.20


def _derive_role(langs: list[str], topics: list[str]) -> tuple[str | None, float]:
    """Very rough role-type hint from stack signals. Low confidence by nature."""
    blob = " ".join(langs + topics).lower()
    if any(k in blob for k in ("react", "vue", "svelte", "css", "frontend", "tailwind")):
        if any(k in blob for k in ("node", "api", "backend", "python", "go", "rails")):
            return "Fullstack", 0.4
        return "Frontend", 0.4
    if any(k in blob for k in ("kubernetes", "terraform", "docker", "devops", "ansible")):
        return "DevOps", 0.45
    if any(k in blob for k in ("ml", "tensorflow", "pytorch", "data", "jupyter")):
        return "Data", 0.4
    if any(k in blob for k in ("go", "rust", "java", "python", "api", "backend")):
        return "Backend", 0.4
    return None, 0.0


def enrich(gravatar_login: str | None, email: str) -> dict:
    """Resolve a GitHub identity and build fields. Returns dict with status."""
    out: dict = {
        "resolved": False,
        "match_confidence": 0.0,
        "fields": {},
        "login": None,
        "candidates": [],
        "rate_limited": False,
    }

    candidates: list[tuple[str, str, float]] = []
    if gravatar_login:
        candidates.append(("gravatar_link", gravatar_login, 0.85))
    local = re.sub(r"[^a-zA-Z0-9-]", "", email.split("@", 1)[0])
    if local and local.lower() != (gravatar_login or "").lower():
        candidates.append(("email_derived", local, 0.35))

    chosen = None
    for origin, login, base in candidates:
        user, err = _user(login)
        if err == "rate_limited":
            out["rate_limited"] = True
            return out
        if user:
            chosen = (origin, login, base, user)
            break
        out["candidates"].append({"login": login, "origin": origin, "verified": False})

    if not chosen:
        return out

    origin, login, conf, user = chosen

    # Don't resolve low-confidence guesses into the profile — record as a
    # candidate only. This prevents an email-local-part match (conf=0.35)
    # from being emitted as canonical_identity.
    if conf < MIN_RESOLVE_CONFIDENCE:
        out["candidates"].append({"login": login, "origin": origin, "verified": False})
        return out

    out.update(resolved=True, match_confidence=conf, login=login)
    src = [{"provider": "github"}]

    def put(k: str, v: object, c: float, derived: bool = False) -> None:
        if v not in (None, "", []):
            out["fields"][k] = field(v, c, src, derived=derived)

    put("full_name", user.get("name"), min(0.9, conf + 0.1))
    put("github_login", user.get("login"), conf)
    put("github_url", user.get("html_url"), conf)
    put("bio", user.get("bio"), conf)
    put("location", user.get("location"), conf)
    if user.get("company"):
        put("current_company", user["company"].lstrip("@"), conf)
    put("personal_website", user.get("blog"), conf)
    put("avatar_url", user.get("avatar_url"), 0.9)
    if user.get("twitter_username"):
        put("social_links",
            [{"service": "twitter", "url": f"https://x.com/{user['twitter_username']}"}],
            conf)
    put("account_created_at", user.get("created_at"), 0.9)

    langs, repo_count, topics = _repo_languages(login)
    if langs:
        put("languages", langs[:5], min(0.75, conf), derived=True)
    if topics:
        put("interests_topics", topics, 0.5, derived=True)
    put("public_repo_count", repo_count, 0.8)

    contributions = _contribution_activity(login)
    if contributions is not None:
        put("annual_contributions", contributions, 0.85)

    oss, oss_c = _derive_oss(repo_count, contributions)
    put("open_source_activity", oss, oss_c, derived=True)

    sen, sen_c = _derive_seniority(user.get("created_at"))
    if sen:
        put("seniority", sen, sen_c, derived=True)

    role, role_c = _derive_role(langs, topics)
    if role:
        put("role_type", role, role_c, derived=True)

    return out
