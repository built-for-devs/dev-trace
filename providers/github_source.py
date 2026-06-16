"""GitHub provider (Depth 2). Free; GITHUB_TOKEN strongly recommended.

Covers: identity resolution (email -> login), public profile, byte-weighted
languages via /languages, contribution activity via GraphQL (token only),
and derived ICP basics (seniority, role hint, OSS activity).
"""
from __future__ import annotations
import os
import re
import requests
from datetime import datetime, timezone

from .envelope import field

REST = "https://api.github.com"
GQL = "https://api.github.com/graphql"


def _headers():
    h = {"User-Agent": "trace-local/0.2", "Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _user(login):
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


def _repo_languages(login, max_repos=25):
    """Byte-weighted language tally across recent owned, non-fork repos."""
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

    lang_bytes, topics = {}, {}
    count = 0
    for repo in repos:
        if repo.get("fork"):
            continue
        count += 1
        for t in (repo.get("topics") or []):
            topics[t] = topics.get(t, 0) + 1
        # byte-weighted languages (one extra call per repo; cap to first 12)
        if count <= 12 and repo.get("languages_url"):
            try:
                lr = requests.get(repo["languages_url"], headers=_headers(),
                                  timeout=8)
                if lr.status_code == 200:
                    for lang, b in lr.json().items():
                        lang_bytes[lang] = lang_bytes.get(lang, 0) + b
            except Exception:
                pass
        # fallback: primary language
        elif repo.get("language"):
            lang_bytes[repo["language"]] = lang_bytes.get(repo["language"], 0) + 1
    ranked_langs = sorted(lang_bytes, key=lang_bytes.get, reverse=True)
    ranked_topics = sorted(topics, key=topics.get, reverse=True)[:10]
    return ranked_langs, count, ranked_topics


def _contribution_activity(login):
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
def _derive_oss(repo_count, contributions):
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


def _derive_seniority(created_at):
    if not created_at:
        return None, 0.0
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except Exception:
        return None, 0.0
    years = (datetime.now(timezone.utc) - created).days / 365.25
    if years < 3:
        return "Early Career", 0.4
    if years < 8:
        return "Senior", 0.45
    return "Leadership", 0.4


def _derive_role(langs, topics):
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


def enrich(gravatar_login, email):
    """Resolve a GitHub identity and build fields. Returns dict with status."""
    out = {"resolved": False, "match_confidence": 0.0, "fields": {},
           "login": None, "candidates": [], "rate_limited": False}

    candidates = []
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
    out.update(resolved=True, match_confidence=conf, login=login)
    src = [{"provider": "github"}]

    def put(k, v, c, derived=False):
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
