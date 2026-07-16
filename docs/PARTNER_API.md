# CommuniQ Partner API (v1)

Server-to-server API for B2B partners to transcribe, synthesize, and **check call audio for
correctness** against their own knowledge base and scoring rubric.

- **Base URL:** `https://ai.communiq.ge/api`  (all paths below are relative to this)
- **Interactive docs / OpenAPI:** `https://ai.communiq.ge/api/docs` · schema at `/api/openapi.json`
- **Auth:** send your per-tenant key in the **`X-API-Key`** header on every request.
  ```
  X-API-Key: cq_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  ```
  (A tenant login Bearer token, `Authorization: Bearer <token>`, also works.) Get your key from
  your CommuniQ account manager. Everything you access is scoped to your tenant only.

> All partner endpoints are versioned under **`/v1`**. Confirm your identity any time with
> `GET /v1/account`.

---

## Quick start

```bash
KEY=cq_your_key_here
BASE=https://ai.communiq.ge/api

# who am I?
curl -s $BASE/v1/account -H "X-API-Key: $KEY"

# check one call synchronously (~20-30s): transcript + analysis + KB fact-check + score
curl -s -X POST $BASE/v1/analyze -H "X-API-Key: $KEY" -F "file=@call.mp3"
```

---

## Endpoints

### Account
`GET /v1/account` → your `client_id`, name, KB stats, active rubric version, and usage.

### Knowledge base (RAG source for correctness checks)
| | |
|---|---|
| `POST /v1/kb/documents/upload` | upload a file (PDF / DOCX / TXT / MD), multipart `file=` |
| `POST /v1/kb/documents/text` | `{title, doc_type, text, tags[]}` |
| `POST /v1/kb/documents/csv` | upload a Q&A / key-value CSV |
| `GET /v1/kb/documents` · `GET /v1/kb/documents/{id}` · `/{id}/chunks` | list / read |
| `DELETE /v1/kb/documents/{id}` | delete |
| `POST /v1/kb/search` | `{query}` → semantic search over your KB |

Docs ingest asynchronously (status `pending → processing → ready`). Poll `GET /v1/kb/documents`.

### Scoring rubric
`GET /v1/scoring/config` · `PUT /v1/scoring/config` — define the weighted dimensions your calls
are scored against. Body: `{dimensions:[{name, weight, guidance}], rubric}`. **Weights must total
100%.**

### Speech
- `POST /v1/transcriptions` — multipart `file=` → `{transcript, language, words[]}` (STT only).
- `POST /v1/tts` — `{text, voice_id?, language_code?}` → `audio/mpeg` bytes. Voices: `GET /v1/voices`,
  languages: `GET /v1/languages` (EN / RU / **Georgian**).

### Correctness checking (the core)
Every check returns **analysis** (summary, sentiment, topics, key points, quality), **`kb_check`**
(each factual claim marked `SUPPORTED` / `CONTRADICTED` / `NOT_IN_KB` with evidence + an overall
accuracy score), and **`scoring`** (per-dimension score + weighted total against your rubric).

- `POST /v1/analyze` — **synchronous**, one file. Blocks ~20-30s, returns the full result. Best
  for one-offs / testing.
- `POST /v1/analyses` — **async**, one file. Returns `202 {id, status:"queued"}`. Poll
  `GET /v1/jobs/{id}`.
- `POST /v1/analyses/batch` — **async bulk**, up to **50** files (multipart, repeat `files=`; ≤25 MB
  each). Returns `202 {batch_id, jobs:[…]}`. Poll `GET /v1/analyses/batch/{batch_id}`.

### Results
- `GET /v1/jobs/{id}` — full result for one job.
- `GET /v1/jobs?status=&batch_id=&limit=&offset=` — list your jobs (paginated, `next_offset`).
- `GET /v1/analyses/batch/{batch_id}` — `{complete, totals:{done,error,…}, jobs:[…]}`.

---

## Async lifecycle

```bash
# submit
JOB=$(curl -s -X POST $BASE/v1/analyses -H "X-API-Key: $KEY" \
        -F "file=@call.mp3" -F "external_ref=call-42" | jq -r .id)

# poll every ~5-10s (each job takes ~30s) until status is done|error
curl -s $BASE/v1/jobs/$JOB -H "X-API-Key: $KEY" | jq '{status, kb_check:.kb_check.accuracy_score, score:.scoring.weighted_total}'
```

**Bulk:**
```bash
curl -s -X POST $BASE/v1/analyses/batch -H "X-API-Key: $KEY" \
  -F "files=@a.mp3" -F "files=@b.mp3" -F "external_refs=a" -F "external_refs=b"
# -> {"batch_id":"…","jobs":[{"id":"…","external_ref":"a","status":"queued"}, …]}

curl -s $BASE/v1/analyses/batch/<batch_id> -H "X-API-Key: $KEY"
# -> {"complete":true,"totals":{"done":2},"jobs":[…]}
```

Job status flow: `queued → transcribing → analyzing → done | error`.

## Idempotency
Pass an **`external_ref`** (your own id) per item — or an `Idempotency-Key` header for single
submits. A repeat with the same `external_ref` returns the **existing** job instead of re-running
(and re-billing). A previously *failed* ref can be resubmitted to retry it.

## Errors
JSON `{ "detail": "…" }` with standard codes: `400` bad input, `401` missing/invalid key,
`404` not found / not yours, `413` too large, `429` rate-limited, `502` upstream (STT/LLM) error.
The analysis itself never partially fails silently — a failed job has `status:"error"` and an
`error` message.

## Limits & notes
- Single upload ≤ 100 MB; batch ≤ 50 files, ≤ 25 MB each.
- Audio is **not stored** after processing; results are retained. If the service restarts mid-job,
  that job is marked `error` — resubmit it (idempotency makes this safe).
- Everything is strictly tenant-isolated: you can only ever see your own KB, jobs, and rubric.
