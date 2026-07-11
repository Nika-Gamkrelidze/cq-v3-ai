# CQ v3 AI — Roadmap

Prioritized. See root `CLAUDE.md` for full context. Nothing here contains secrets.

## Status snapshot
- **Deployed:** audio analysis, ElevenLabs STT + TTS (EN/RU/Georgian), knowledge base + imports,
  KB admin console, multi-tenancy + isolation, RAG fact-check, per-tenant weighted scoring rubric,
  three trilingual UIs, single sign-in, push-to-deploy webhook (server side).
- **Local, unpushed:** 7 QA bug fixes on 2 commits ahead of `origin/main` (see `CLAUDE.md` §6).
- **Local QA:** fully green.

## Now (unblock the pipeline)
1. **Register the GitHub webhook.** Repo → Settings → Webhooks → Add:
   URL `http://<server>/gh-webhook`, content-type `application/json`, shared secret, event = push,
   SSL verification off (plain HTTP). Exact URL + secret location: `docs/DEPLOYMENT.local.md`.
   Until done, deploys are manual (pull + `docker compose -p cqv3 up -d --build` on the server).
2. **Push + deploy the 2 QA-fix commits** (owner triggers). Non-destructive; volumes untouched.

## Next (production readiness)
3. **HTTPS/TLS.** Currently plain HTTP, no domain. Get a domain → Caddy (auto-certs) or nginx+certbot
   → open 443. Then flip the webhook + API to HTTPS and turn SSL verification on in GitHub.
4. **PII/PHI redaction** before transcripts reach Claude. Compliance-critical for banks/clinics.
   Options: ElevenLabs Scribe entity detection to redact, or a redaction pass pre-LLM. Clinics also
   need a HIPAA BAA with ElevenLabs (sales-gated — start early).
5. **Production hardening:**
   - Lock CORS to the real front-end domain(s) (currently `*`).
   - Rotate all API keys / secrets that were used during development.
   - Introduce **Alembic** migrations (today: idempotent `db/*.sql` applied on startup — fine for
     additive changes, insufficient for real column/type changes).
   - Automated tests + CI (there is a tenant-isolation test in `backend/tests/`; expand it and wire
     a GitHub Action to run on PRs before the deploy webhook fires).
   - Rate limiting / abuse protection on the public endpoints beyond the daily anon quota.
   - Backups for the `pgdata` volume.

## Optional (original-spec integration path)
6. **PHP batch ingestion.** The original design had an external PHP app POST call metadata + an audio
   URI to `POST /calls` for end-of-day batch scoring. Not built. Would need:
   - Audio storage (S3 / DigitalOcean Spaces) and a way to fetch each `audio_uri`.
   - Background/queue workers (transcribe → score) instead of the current synchronous upload flow.
   - The Anthropic **Batch API** (50% off) + prompt caching for cost at volume.
   Only pursue if the PHP-integration use case is actually needed alongside the self-serve app.

## Optional (polish)
7. Bulk audio upload; richer/scheduled exports; per-tenant statistics dashboards; notifications for
   flagged calls (e.g. CONTRADICTED claims or low rubric scores); operator assignment / review
   workflow; golden-set evaluation harness to tune the scoring model choice (Haiku/Sonnet/Opus).

## Known small/latent items (non-blocking)
- `res.endswith("0")` delete-count checks in `kb_admin.py` / `kb.py` are technically wrong for counts
  ending in 0, but only ever target a single row by id, so unreachable. Tidy if touched.
- `webhook.py` `int(Content-Length)` would raise on a non-numeric header; GitHub always sends a valid
  one and the HMAC check rejects forged bodies. Low priority.
