# CQ v3 AI — Roadmap

Prioritized. See root `CLAUDE.md` for full context. Nothing here contains secrets.

## Status snapshot
- **Deployed & live at https://ai.communiq.ge:** audio analysis, STT + TTS (EN/RU/Georgian),
  knowledge base + imports, KB admin console, multi-tenancy + isolation, RAG fact-check,
  per-tenant weighted scoring rubric, single sign-in, the QA fixes; the **multi-provider AI
  registry** (text: Anthropic / OpenAI / Gemini · speech-to-text: ElevenLabs / OpenAI / Gemini
  (`gemini-3.5-transcribe`) · text-to-speech: ElevenLabs / OpenAI; per-workspace
  assignment + tenants' own keys, encrypted at rest); the **chat bot + operator copilot**
  (`/v1/chat/*`, consumed by Swift Chat — `docs/CHAT_INTEGRATION.md`); the **KB curation loop**
  run by the **`cq-worker`** process (also the suggestion reaper, queued re-embeds, retention and
  the health purge); the **Server health** console tab (`/admin/health/*`, `db/health.sql`:
  host/api/DB samples, 1h–30d charts, per-tenant load share); and the **Next.js static frontend**
  (the legacy pages are deleted). `origin/main` == server.
- **Auto-deploy:** ✅ GitHub webhook registered (hook `651713539`) — every push to `main` deploys.
- **Local QA:** fully green.

## Now
- ~~Register the GitHub webhook~~ ✅ done — pushes auto-deploy (no VPN needed).
- ~~Push + deploy the QA-fix commits~~ ✅ done, verified live.
- **Chat bot pilot go-live.** Code is on both sides (Swift Chat `ca1a3aa` / `47f2539`, plus the
  2026-09-09 audit fix pass); what remains is operational: `SECRETS_KEY` on the server, a tested
  default `llm` connection, default-bot greeting/refusal copy in en/ka/ru, a shared KB document +
  Autopilot on for the pilot tenant, the chat-connections credential (all four scopes) pasted into
  the Swift Chat SuperAdmin *CQ AI connection* card (encrypted in its DB; the env var is only a
  bootstrap import), `client_id` mapped in the Swift Chat admin, the widget probes and the
  kill-switch drill. The ordered checklist lives in root `CLAUDE.md` §6.
- **Pick the Georgian speech-to-text engine on evidence.** ElevenLabs Scribe mishears Georgian
  domain terms (the reported case: *36 თვემდე* → *36 წლამდე*). Both levers now exist —
  Scribe with a language code, key terms and an audio format (superadmin < tenant < per file),
  and a verified `gemini-3.5-transcribe` connection. What is missing is the comparison: run the
  same Georgian call through each, judge the numbers and the domain nouns, then set the default
  connection. Note the trade: Gemini's key terms are unavailable while speaker separation is on.
- **Refresh the Georgian user guides** (`docs/guides/*.docx`, written 2026-09-03). Verified
  stale in three places: the superadmin guide's *Integrations* section still says the Anthropic
  and ElevenLabs keys live there (they moved to the **AI providers** tab), and it sends the
  reader to a header link named *ცოდნის ბაზა* (renamed **AI Services**); undocumented
  everywhere are *AI providers*, *Transcription* and *Server health* for the operator, and the
  workspace's own-AI-key tab for tenants. They name no page URLs, so the Next.js cutover itself
  did not invalidate them.

## Next (production readiness)
3. ~~HTTPS/TLS~~ ✅ done — Let's Encrypt terminated by `cq-web` (`deploy/tls-ssl.conf`); port 80
   redirects and keeps serving the ACME challenge. Remaining nit: the GitHub webhook still points
   at the plain-HTTP IP with `insecure_ssl` — flip it to `https://ai.communiq.ge/gh-webhook` and
   turn SSL verification on.
4. **PII/PHI redaction** before transcripts reach Claude. Compliance-critical for banks/clinics.
   Options: ElevenLabs Scribe entity detection to redact, or a redaction pass pre-LLM. Clinics also
   need a HIPAA BAA with ElevenLabs (sales-gated — start early).
5. **Production hardening:**
   - Lock CORS to the real front-end domain(s) (currently `*`).
   - Rotate all API keys / secrets that were used during development.
   - Introduce **Alembic** migrations (today: idempotent `db/*.sql` applied on startup — fine for
     additive changes, insufficient for real column/type changes).
   - Automated tests + CI (`backend/tests/` covers isolation, act-as-tenant, chat-store SQL and
     the provider adapters; wire a GitHub Action to run them on PRs before the deploy webhook
     fires).
   - Rate limiting / abuse protection on the public endpoints beyond the daily anon quota.
   - Backups for the `pgdata` volume.

## Optional (original-spec integration path)
6. **PHP batch ingestion.** The original design had an external PHP app POST call metadata + an audio
   URI to `POST /calls` for end-of-day batch scoring. Not built. Would need:
   - Audio storage (S3 / DigitalOcean Spaces) and a way to fetch each `audio_uri`.
   - Background/queue workers (transcribe → score) instead of the current synchronous upload flow
     (`cq-worker` exists, but for chat/curation/retention duties — it deliberately runs no
     migrations and no analysis sweeps, so a scoring worker would be a new duty, not a new process).
   - The Anthropic **Batch API** (50% off) + prompt caching for cost at volume.
   Only pursue if the PHP-integration use case is actually needed alongside the self-serve app.

## Optional (polish)
7. Bulk audio upload; richer/scheduled exports; per-tenant statistics dashboards; notifications for
   flagged calls (e.g. CONTRADICTED claims or low rubric scores); operator assignment / review
   workflow; golden-set evaluation harness to tune the scoring model choice (Haiku/Sonnet/Opus).
8. Server health follow-ups: alert thresholds (CPU / disk / error-rate → email or Swift Chat
   notice), CSV/JSON export of `system_metrics` and the per-tenant load table, per-tenant quotas
   fed by the same `tenant_load` counters.

## Known small/latent items (non-blocking)
- `res.endswith("0")` delete-count checks in `kb_admin.py` / `kb.py` are technically wrong for counts
  ending in 0, but only ever target a single row by id, so unreachable. Tidy if touched.
- `webhook.py` `int(Content-Length)` would raise on a non-numeric header; GitHub always sends a valid
  one and the HMAC check rejects forged bodies. Low priority.
