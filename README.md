# Trace

Turn an email into a public developer profile. **Open source, free to run.**
Bring your own API keys to unlock deeper layers — Trace just sets up the
plumbing so adding a key is one line.

```bash
pip install -r requirements.txt
python3 trace.py someone@example.com --pretty
```

That works with **zero keys**. The free layers run immediately.

## The waterfall

| Depth | Layer | Source | Key | Runs by default? |
|-------|-------|--------|-----|------------------|
| 0 | validate | syntax + MX + disposable | — | ✅ always, free |
| 1 | surface | **Gravatar** + domain scrape | — | ✅ always, free |
| 1 | verify | Hunter.io email verification | `HUNTER_API_KEY` | only if key added |
| 2 | profile | **GitHub** identity + ICP basics | — (`GITHUB_TOKEN` recommended) | ✅ always, free |
| 3 | enriched | Tabstack web/company | `TABSTACK_API_KEY` | only if key added |
| 4 | deep | SixtyFour completeness | `SIXTYFOUR_API_KEY` | only if key added |

**Gravatar and GitHub are core and always run — no key required.** They have no
line in `.env` because they need no key, not because they're optional. Any keyed
layer without its key is skipped and reported in `meta.depths`, never an error.

## Adding your keys

Every key is optional. Add only the ones you have:

```bash
cp .env.example .env     # then open .env and paste in your keys
```

`.env`:

```
GITHUB_TOKEN=ghp_xxx          # free; raises GitHub limit 60/hr -> 5000/hr
HUNTER_API_KEY=xxx            # free tier ~25-50 verifications/mo
TABSTACK_API_KEY=xxx          # paid
SIXTYFOUR_API_KEY=xxx         # paid
```

Where to get them:
- **GitHub token** (free, recommended): https://github.com/settings/tokens — no
  scopes needed for public data.
- **Hunter** (freemium): https://hunter.io
- **Tabstack** (paid): your Tabstack account
- **SixtyFour** (paid): your SixtyFour account

`.env` is gitignored — your keys never get committed. `.env.example` (with empty values) is intentionally committed as a template.

## Output

`{ "meta": {...}, "profile": {...} }`. Every field is wrapped:

```json
{ "value": "...", "confidence": 0.0-1.0, "public": true,
  "sources": [...], "derived": false, "conflicts": [] }
```

- `meta.depths` — which layers ran / skipped_no_key / rate_limited.
- `meta.match_status` — `verified` | `probable` | `unverified_candidates`. When an
  email can't be confidently tied to an identity, Trace returns candidates rather
  than guessing. It fails safe instead of inventing data.

## Principles

- **Public data only.** Nothing is emitted that can't be sourced publicly.
- **Honest confidence.** Derived fields (seniority, role) carry low confidence on
  purpose — they're inferences, not facts.
- **No invented data.** Below a match-confidence threshold, Trace returns
  unverified candidates instead of fusing a wrong guess.

## Notes

- `dnspython` enables real MX checks; without it MX is skipped gracefully.
  Install with `pip install dnspython` if you want MX validation.
- Gravatar lookups include the MD5 hash of the email in the request URL.
  This hash is visible to network observers and can be reversed given a
  candidate email list. Don't run Trace on networks where that is a concern.
- Tabstack/SixtyFour request shapes are scaffolds — confirm endpoints/fields
  against current API docs before relying on paid output.

## License

MIT — see [LICENSE](LICENSE).
