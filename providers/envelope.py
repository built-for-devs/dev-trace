"""Shared field envelope and merge helpers for Trace."""
from __future__ import annotations


def field(value, confidence, sources, public=True, derived=False, conflicts=None):
    """Wrap a value in the standard Trace field envelope."""
    return {
        "value": value,
        "confidence": round(float(confidence), 2),
        "public": public,
        "sources": sources,
        "derived": derived,
        "conflicts": conflicts or [],
    }


def merge_field(profile, key, new):
    """Merge a new field into the profile.

    - Skips empty values.
    - On agreement: unions sources, bumps confidence slightly (corroboration).
    - On conflict: keeps the higher-confidence value, records the other in
      `conflicts` rather than silently dropping it.
    """
    if new is None or new.get("value") in (None, "", [], {}):
        return
    if key not in profile:
        profile[key] = new
        return
    existing = profile[key]
    if existing["value"] == new["value"]:
        for s in new["sources"]:
            if s not in existing["sources"]:
                existing["sources"].append(s)
        existing["confidence"] = round(min(0.99, existing["confidence"] + 0.05), 2)
        return
    # conflict
    loser = {
        "value": existing["value"],
        "sources": existing["sources"],
        "confidence": existing["confidence"],
    }
    if new["confidence"] > existing["confidence"]:
        existing["conflicts"].append(loser)
        existing["value"] = new["value"]
        existing["confidence"] = new["confidence"]
        existing["sources"] = new["sources"]
        existing["derived"] = new.get("derived", False)
    else:
        existing["conflicts"].append({
            "value": new["value"],
            "sources": new["sources"],
            "confidence": new["confidence"],
        })
