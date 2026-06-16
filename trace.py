#!/usr/bin/env python3
"""
Trace (local) — full-spec developer enrichment, free to run, bring your own keys.

email in -> public developer profile out.

Waterfall (each layer self-disables if its key is missing):
  Depth 0  validate    : syntax + MX + disposable        [free, no key]
  Depth 1  surface      : Gravatar + free domain scrape   [free, no key]
           verify       : Hunter.io email verification    [HUNTER_API_KEY]
  Depth 2  profile      : GitHub identity + ICP basics     [free; GITHUB_TOKEN ++]
  Depth 3  enriched      : Tabstack web/company            [TABSTACK_API_KEY]
  Depth 4  deep          : SixtyFour completeness          [SIXTYFOUR_API_KEY]

Public data only. No storage. No billing. Runs locally.
Every field carries an envelope: value / confidence / public / sources / derived.

Keys go in a .env file (see .env.example) or real environment variables.
Missing keys -> that depth is skipped and reported, never an error.

Usage:
    python3 trace.py someone@example.com --pretty
    python3 trace.py someone@example.com --depth 2
    python3 trace.py someone@example.com --max-depth 4   # use everything you have keys for
"""
from __future__ import annotations
import sys
import os
import json
import argparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # .env optional; real env vars still work

from providers.envelope import field, merge_field
from providers import free_sources as free
from providers import github_source as gh
from providers import keyed_sources as keyed

DEPTH_NAMES = {0: "validate", 1: "surface", 2: "profile", 3: "enriched", 4: "deep"}


def trace(email, max_depth=4):
    email = email.strip()
    profile = {}
    meta = {
        "queried_with": {"email": email},
        "depth_reached": 0,
        "depths": {},          # per-depth status: ran / skipped_no_key / rate_limited
        "match_status": "unverified_candidates",
        "candidates": [],
    }

    def mark(depth, status):
        meta["depths"][f"{depth}_{DEPTH_NAMES[depth]}"] = status

    # ---- Depth 0: validate ----
    d0 = free.validate(email)
    meta["validation"] = d0["signals"]
    base_conf = d0["confidence_contrib"]
    if not d0["signals"].get("syntax_valid"):
        meta["error"] = "invalid_email_syntax"
        mark(0, "failed")
        return {"meta": meta, "profile": profile}
    mark(0, "ran")
    if d0["is_disposable"]:
        meta["warning"] = "disposable_email_domain"
    meta["depth_reached"] = 0
    domain = d0["domain"]
    if domain and not d0["is_freemail"]:
        merge_field(profile, "company_domain",
                    field(domain, 0.6, [{"provider": "email_parse"}]))
    if max_depth == 0:
        meta["overall_confidence"] = round(base_conf, 2)
        return {"meta": meta, "profile": profile}

    # ---- Depth 1: Gravatar + free domain scrape + optional Hunter ----
    g = free.gravatar(email)
    if g["found"]:
        for k, v in g["fields"].items():
            merge_field(profile, k, v)
        if g["social_links"]:
            merge_field(profile, "social_links",
                        field(g["social_links"], 0.7, [{"provider": "gravatar"}]))
    mark(1, "ran")

    if domain and not d0["is_freemail"]:
        dc = free.domain_company(domain)
        for k, v in dc["fields"].items():
            merge_field(profile, k, v)

    hunter = keyed.hunter_verify(email)
    meta["depths"]["hunter_verify"] = hunter["status"]
    if hunter["status"] == "ok":
        for k, v in hunter["fields"].items():
            merge_field(profile, k, v)
        base_conf += hunter.get("confidence_contrib", 0.0)

    meta["depth_reached"] = 1
    if max_depth == 1:
        oc = base_conf + (0.15 if g["found"] else 0.0)
        meta["overall_confidence"] = round(min(0.99, oc), 2)
        meta["match_status"] = "probable" if g["found"] else "unverified_candidates"
        return {"meta": meta, "profile": profile}

    # ---- Depth 2: GitHub ----
    gres = gh.enrich(g.get("github_login"), email)
    meta["candidates"] = gres.get("candidates", [])
    if gres.get("rate_limited"):
        mark(2, "rate_limited")
    elif gres["resolved"]:
        mark(2, "ran")
        for k, v in gres["fields"].items():
            merge_field(profile, k, v)
        meta["match_confidence"] = gres["match_confidence"]
        meta["github_login"] = gres["login"]
        meta["canonical_identity"] = f"gh:{gres['login']}"
        meta["match_status"] = ("verified" if gres["match_confidence"] >= 0.7
                                else "probable")
    else:
        mark(2, "no_match")
    meta["depth_reached"] = 2

    # resolved name helps downstream paid lookups
    resolved_name = (profile.get("full_name") or {}).get("value")
    website = (profile.get("company_website") or profile.get("personal_website")
               or {}).get("value")

    if max_depth == 2:
        meta["overall_confidence"] = _blend(base_conf, g["found"], gres)
        return {"meta": meta, "profile": profile}

    # ---- Depth 3: Tabstack ----
    if domain and not d0["is_freemail"]:
        tab = keyed.tabstack_enrich(domain, name=resolved_name, website=website)
    else:
        tab = keyed._skip("tabstack")
    meta["depths"]["3_enriched"] = tab["status"]
    if tab["status"] == "ok":
        for k, v in tab["fields"].items():
            merge_field(profile, k, v)
    meta["depth_reached"] = 3  # attempted regardless of ok/skipped_no_key
    if max_depth == 3:
        meta["overall_confidence"] = _blend(base_conf, g["found"], gres)
        return {"meta": meta, "profile": profile}

    # ---- Depth 4: SixtyFour (customer-scoped, public fields only) ----
    sf = keyed.sixtyfour_enrich(email, name=resolved_name)
    meta["depths"]["4_deep"] = sf["status"]
    if sf["status"] == "ok":
        meta["sixtyfour_scope"] = "customer_only"
        for k, v in sf["fields"].items():
            merge_field(profile, k, v)
    meta["depth_reached"] = 4  # attempted regardless of ok/skipped_no_key

    meta["overall_confidence"] = _blend(base_conf, g["found"], gres)
    return {"meta": meta, "profile": profile}


def _blend(base_conf, gravatar_found, gres):
    oc = base_conf
    if gravatar_found:
        oc += 0.15
    if gres.get("resolved"):
        oc += gres.get("match_confidence", 0) * 0.4
    return round(min(0.99, oc), 2)


def main():
    ap = argparse.ArgumentParser(description="Trace (local) — full-spec free enrichment")
    ap.add_argument("email")
    ap.add_argument("--max-depth", type=int, default=4, choices=[0, 1, 2, 3, 4],
                    help="ceiling: 0 validate .. 4 deep. Keyed depths skip if no key.")
    ap.add_argument("--depth", type=int, choices=[0, 1, 2, 3, 4],
                    help="alias for --max-depth")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()
    depth = args.depth if args.depth is not None else args.max_depth
    result = trace(args.email, max_depth=depth)
    print(json.dumps(result, indent=2 if args.pretty else None, default=str))


if __name__ == "__main__":
    main()
