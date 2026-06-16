"""Shared field envelope, merge helpers, and shared constants for Trace."""
from __future__ import annotations

# Shared user-agent used by all providers (single source of truth)
UA: dict[str, str] = {"User-Agent": "trace-local/0.2"}


def field(
    value: object,
    confidence: float,
    sources: list[dict],
    public: bool = True,
    derived: bool = False,
    conflicts: list | None = None,
) -> dict:
    """Wrap a value in the standard Trace field envelope."""
    return {
        "value": value,
        "confidence": round(float(confidence), 2),
        "public": public,
        "sources": sources,
        "derived": derived,
        "conflicts": conflicts or [],
    }


def merge_field(profile: dict, key: str, new: dict | None) -> None:
    """Merge a new field into the profile.

    Always reassigns profile[key] rather than mutating in place, so callers
    holding an earlier reference to a field observe a stable snapshot.

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
        merged_sources = existing["sources"] + [
            s for s in new["sources"] if s not in existing["sources"]
        ]
        profile[key] = {
            **existing,
            "sources": merged_sources,
            "confidence": round(min(0.99, existing["confidence"] + 0.05), 2),
        }
        return
    # conflict — keep the higher-confidence value, record the loser
    loser = {
        "value": existing["value"],
        "sources": existing["sources"],
        "confidence": existing["confidence"],
    }
    if new["confidence"] > existing["confidence"]:
        profile[key] = {**new, "conflicts": existing["conflicts"] + [loser]}
    else:
        profile[key] = {
            **existing,
            "conflicts": existing["conflicts"] + [
                {
                    "value": new["value"],
                    "sources": new["sources"],
                    "confidence": new["confidence"],
                }
            ],
        }
