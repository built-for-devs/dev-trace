"""Opt-in keyed providers. Each returns status 'skipped_no_key' when its
env var is absent, so the waterfall runs fully without any of them.

NOTE: Tabstack and SixtyFour request/response shapes are best-effort scaffolds.
Endpoints and field names should be confirmed against current API docs; the
contract here (key -> enrich -> return fields in the envelope) is what matters
and is stable. Verify before relying on the paid output.
"""
from __future__ import annotations
import os
import requests

from .envelope import field, UA


def _skip(name: str) -> dict:
    return {"status": "skipped_no_key", "provider": name, "fields": {}}


# --------------------------------------------------------------------------
# Hunter.io — email verification (freemium, ~25-50 verifications/mo free)
# --------------------------------------------------------------------------
def hunter_verify(email: str) -> dict:
    api_key = os.environ.get("HUNTER_API_KEY")
    if not api_key:
        return _skip("hunter")
    out: dict = {"status": "ok", "provider": "hunter", "fields": {},
                 "confidence_contrib": 0.0}
    try:
        r = requests.get("https://api.hunter.io/v2/email-verifier",
                         params={"email": email, "api_key": api_key},
                         headers=UA, timeout=12)
    except Exception:
        out["status"] = "error"
        return out
    if r.status_code != 200:
        out["status"] = f"http_{r.status_code}"
        return out
    data = r.json().get("data", {})
    src = [{"provider": "hunter"}]
    status = data.get("status")  # valid / invalid / accept_all / webmail / ...
    score = data.get("score")    # 0-100
    if status:
        out["fields"]["email_verification"] = field(status, 0.85, src)
    if score is not None:
        out["confidence_contrib"] = min(0.3, (score / 100.0) * 0.3)
    if data.get("sources"):
        out["fields"]["email_found_on_web"] = field(
            len(data["sources"]), 0.6, src, derived=True)
    return out


# --------------------------------------------------------------------------
# Tabstack — public web/company enrichment (paid; BYO key)
# --------------------------------------------------------------------------
def tabstack_enrich(domain: str, name: str | None = None, website: str | None = None) -> dict:
    api_key = os.environ.get("TABSTACK_API_KEY")
    if not api_key:
        return _skip("tabstack")
    out: dict = {"status": "ok", "provider": "tabstack", "fields": {}}
    target = website or (f"https://{domain}" if domain else None)
    if not target:
        out["status"] = "no_target"
        return out
    headers = {**UA, "Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    try:
        # /extract/json against the company site — confirm schema vs. current docs
        r = requests.post(
            "https://api.tabstack.ai/extract/json",
            headers=headers,
            json={"url": target,
                  "schema": {"company_name": "string", "industry": "string",
                             "description": "string", "company_size": "string"}},
            timeout=30)
    except Exception:
        out["status"] = "error"
        return out
    if r.status_code != 200:
        out["status"] = f"http_{r.status_code}"
        return out
    try:
        data = r.json()
    except Exception:
        out["status"] = "bad_json"
        return out
    payload = data.get("data", data)  # tolerate either envelope
    src = [{"provider": "tabstack"}]
    for field_name, conf in (("company_name", 0.7), ("industry", 0.65),
                              ("company_size", 0.55), ("description", 0.6)):
        val = payload.get(field_name)
        if val:
            out["fields"][field_name] = field(val, conf, src, derived=True)
    return out


# --------------------------------------------------------------------------
# SixtyFour — completeness backstop (paid; BYO key; PUBLIC FIELDS ONLY)
# --------------------------------------------------------------------------
def sixtyfour_enrich(email: str, name: str | None = None) -> dict:
    api_key = os.environ.get("SIXTYFOUR_API_KEY")
    if not api_key:
        return _skip("sixtyfour")
    out: dict = {"status": "ok", "provider": "sixtyfour", "fields": {},
                 "scope": "customer_only"}
    headers = {**UA, "Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    try:
        # confirm endpoint/shape vs. current SixtyFour docs
        r = requests.post("https://api.sixtyfour.ai/enrich",
                          headers=headers, json={"email": email, "name": name},
                          timeout=30)
    except Exception:
        out["status"] = "error"
        return out
    if r.status_code != 200:
        out["status"] = f"http_{r.status_code}"
        return out
    try:
        data = r.json()
    except Exception:
        out["status"] = "bad_json"
        return out
    payload = data.get("data", data)
    src = [{"provider": "sixtyfour"}]
    # Only surface fields that are public-professional in nature.
    allow = {
        "full_name": 0.75, "current_company": 0.7, "current_title": 0.7,
        "location": 0.65, "seniority": 0.6, "linkedin_url": 0.7,
        "github_url": 0.7, "personal_website": 0.65,
    }
    for field_name, conf in allow.items():
        val = payload.get(field_name)
        if val:
            out["fields"][field_name] = field(val, conf, src, derived=True)
    return out
