"""Free, no-key providers: validate (Depth 0), Gravatar (Depth 1), domain scrape."""
from __future__ import annotations
import re
import hashlib
import socket
import requests

from .envelope import field

UA = {"User-Agent": "trace-local/0.2"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "throwaway.email", "yopmail.com", "trashmail.com", "getnada.com",
    "temp-mail.org", "fakeinbox.com", "sharklasers.com", "dispostable.com",
}
FREEMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "aol.com", "protonmail.com", "proton.me", "live.com", "msn.com",
    "gmx.com", "mail.com", "zoho.com",
}

try:
    import dns.resolver  # type: ignore
    HAVE_DNS = True
except ImportError:
    HAVE_DNS = False


# --------------------------------------------------------------------------
# Depth 0 — Validate
# --------------------------------------------------------------------------
def validate(email):
    out = {"signals": {}, "confidence_contrib": 0.0, "domain": None,
           "is_disposable": False, "is_freemail": False}

    syntax_ok = bool(EMAIL_RE.match(email))
    out["signals"]["syntax_valid"] = syntax_ok
    if not syntax_ok:
        return out

    domain = email.split("@", 1)[1].lower()
    out["domain"] = domain
    out["is_disposable"] = domain in DISPOSABLE_DOMAINS
    out["is_freemail"] = domain in FREEMAIL_DOMAINS

    mx_ok = None
    if HAVE_DNS:
        try:
            mx_ok = len(dns.resolver.resolve(domain, "MX")) > 0
        except Exception:
            mx_ok = False
    else:
        try:
            socket.gethostbyname(domain)
            mx_ok = None  # domain resolves but MX unchecked
        except Exception:
            mx_ok = False

    out["signals"].update({
        "mx_valid": mx_ok,
        "disposable": out["is_disposable"],
        "freemail": out["is_freemail"],
    })

    c = 0.0
    if syntax_ok:
        c += 0.3
    if mx_ok:
        c += 0.3
    if out["is_disposable"]:
        c -= 0.5
    out["confidence_contrib"] = max(0.0, c)
    return out


# --------------------------------------------------------------------------
# Depth 1 — Gravatar
# --------------------------------------------------------------------------
def gravatar(email):
    out = {"found": False, "fields": {}, "github_login": None, "social_links": []}
    h = hashlib.md5(email.strip().lower().encode()).hexdigest()
    try:
        r = requests.get(f"https://en.gravatar.com/{h}.json", headers=UA, timeout=10)
    except Exception:
        return out
    if r.status_code != 200:
        return out
    try:
        entries = r.json().get("entry", [])
    except Exception:
        return out
    if not entries:
        return out

    e = entries[0]
    out["found"] = True
    src = [{"provider": "gravatar"}]

    if e.get("displayName"):
        out["fields"]["full_name"] = field(e["displayName"], 0.7, src)
    if e.get("thumbnailUrl"):
        out["fields"]["avatar_url"] = field(e["thumbnailUrl"], 0.9, src)
    if (e.get("aboutMe") or "").strip():
        out["fields"]["bio"] = field(e["aboutMe"].strip(), 0.7, src)
    if (e.get("currentLocation") or "").strip():
        out["fields"]["location"] = field(e["currentLocation"].strip(), 0.6, src)

    for acct in e.get("accounts", []):
        sn = (acct.get("shortname") or "").lower()
        url = acct.get("url")
        if not url:
            continue
        out["social_links"].append({"service": sn, "url": url})
        if sn == "github":
            m = re.search(r"github\.com/([^/]+)", url)
            if m:
                out["github_login"] = m.group(1)
    for u in e.get("urls", []):
        if u.get("value"):
            out["social_links"].append({"service": "website", "url": u["value"]})
    return out


# --------------------------------------------------------------------------
# Free domain/company scrape (corporate emails only)
# --------------------------------------------------------------------------
def domain_company(domain):
    """Cheap company name guess from the homepage <title>. Free, no key."""
    out = {"fields": {}}
    src = [{"provider": "domain_scrape"}]
    for scheme in ("https://", "http://"):
        try:
            r = requests.get(scheme + domain, headers=UA, timeout=8,
                             allow_redirects=True)
            if r.status_code == 200 and r.text:
                m = re.search(r"<title[^>]*>(.*?)</title>", r.text,
                              re.IGNORECASE | re.DOTALL)
                if m:
                    title = re.sub(r"\s+", " ", m.group(1)).strip()[:120]
                    if title:
                        out["fields"]["company_name"] = field(
                            title, 0.45, src, derived=True)
                out["fields"]["company_website"] = field(
                    scheme + domain, 0.6, src)
                break
        except Exception:
            continue
    return out
