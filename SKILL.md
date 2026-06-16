---
name: trace
description: >
  Enrich a developer's public profile from just an email address. Use when you
  have an email (e.g. a new product signup) and want to know who the person is:
  name, GitHub, company, location, primary languages, open-source activity, and
  a rough seniority/role read. Runs a free waterfall (validate, Gravatar, GitHub,
  domain scrape) and optionally deeper paid layers (Hunter, Tabstack, SixtyFour)
  if the user has supplied those API keys. Public data only. Returns structured
  JSON where every field carries a confidence score and its source. Trigger on
  "who signed up with this email", "enrich this email", "look up this developer",
  "what do we know about someone@domain".
---

# Trace (local)

Turn an email into a public developer profile. Free to run; the user brings
their own API keys to unlock deeper layers. Email in, JSON out.

## The waterfall (each layer self-disables without its key)

- **Depth 0 — Validate** (free): syntax, MX, disposable-domain.
- **Depth 1 — Surface** (free): Gravatar + free company scrape from the email
  domain. Optionally Hunter.io email verification if `HUNTER_API_KEY` is set.
- **Depth 2 — Profile** (free; `GITHUB_TOKEN` recommended): resolve a GitHub
  identity, pull the public profile, byte-weighted languages, contribution
  activity (with token), and derived ICP basics (seniority, role, OSS activity).
- **Depth 3 — Enriched** (`TABSTACK_API_KEY`): Tabstack public web/company data.
- **Depth 4 — Deep** (`SIXTYFOUR_API_KEY`): SixtyFour completeness backstop,
  public professional fields only, flagged customer-scoped.

Any layer missing its key is **skipped and reported in `meta.depths`** — never an
error. With zero keys you still get a real profile from the free layers.

## Output

`{ "meta": {...}, "profile": {...} }`. Every field is wrapped:

```json
{ "value": "...", "confidence": 0.0-1.0, "public": true, "sources": [...], "derived": false }
```

- `meta.depths` — which layers ran / skipped_no_key / rate_limited.
- `meta.match_status` — `verified` | `probable` | `unverified_candidates`. When an
  email can't be confidently tied to a GitHub identity, Trace returns candidates
  rather than fusing a wrong guess. It fails safe instead of inventing data.

## When to use

- A new signup arrives and you only have their email.
- "Who is this person?" given an email.
- A quick ICP read (languages, OSS activity, seniority) on a developer.

## When NOT to use

- You need private or personal-contact data — Trace is public-only by design.

## How to run

```bash
pip install -r requirements.txt
cp .env.example .env          # optional: add any keys you have
python3 trace.py someone@example.com --pretty
python3 trace.py someone@example.com --max-depth 2   # stop after GitHub
```

Keys live in `.env` (see `.env.example`) or real environment variables.

## Notes

- `GITHUB_TOKEN` only needs public-data access; without it, GitHub is limited to
  60 requests/hour shared by IP (you'll see `2_profile: rate_limited`).
- Tabstack/SixtyFour request shapes are scaffolds — confirm against current API
  docs before relying on paid output.
- `dnspython` enables real MX checks; absent, MX is skipped gracefully.
- Public data only. Trace never emits anything it can't source publicly.
