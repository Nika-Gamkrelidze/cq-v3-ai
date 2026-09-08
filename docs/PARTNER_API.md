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
  Accepts the optional `transcription` field described under **Transcription settings** below.
- `POST /v1/tts` — `{text, voice_id?, language_code?, model_id?, voice_settings?, enforce_language?}`
  → `audio/mpeg` bytes. Voices: `GET /v1/voices`, languages: `GET /v1/languages` (EN / RU /
  **Georgian**; each entry carries `model`, the model picked when `model_id` is omitted).
- `GET /v1/tts/models` — the models you may pass as `model_id`, in display order, each with
  `max_chars`, `languages` (ISO codes) and a `supports` block:
  `{presets, style, speaker_boost, speed, language_code: "enforced"|"ignored"|"rejected"}`.
  Legacy and deprecated-alias ids are not listed and are refused.

**Voice settings.** All optional; omit the object entirely to use the voice's own defaults.
`stability` 0–1 · `similarity_boost` 0–1 · `style` 0–1 · `use_speaker_boost` bool ·
`speed` 0.7–1.2. Out-of-range values → **422**. The server then shapes the object for the
model that will actually run and sends only what survives:

| Model | `stability` | `style` | `speed` | `language_code` |
|---|---|---|---|---|
| `eleven_multilingual_v2` (default for EN/RU) | as sent | as sent | as sent | sent, ignored by the model |
| `eleven_v3` (default for KA) | snapped to a preset: 0 Creative · 0.5 Natural · 1 Robust | dropped | dropped | never sent (v3 rejects it); pace and emotion come from tags in the text — `[whispers]`, `[excited]`, ellipses for pauses, CAPITALS for emphasis |
| `eleven_flash_v2_5` | as sent | as sent | as sent | sent and **enforced** |

`similarity_boost` and `use_speaker_boost` pass through on every model. `enforce_language:
false` stops the language code being sent at all (the model then infers the language from
the text); `true` is the default whenever the model accepts one. An unknown or hidden
`model_id` → **400** `{"detail": "…", "code": "model_unavailable"}`.

### Transcription settings

How your audio is sent to the speech model is configurable, because it changes what the model
hears — and the transcript is what fact-checking and scoring then run against. Four settings,
resolved as **code defaults ← deployment default ← your workspace ← this one file**. Each layer
sets only what it changes; anything unset is inherited.

| Field | Type | Meaning |
|---|---|---|
| `language_code` | ISO-639-1/3 string, or `null` | The language to expect. `null` = let the model detect it. A hint, not enforcement — but a strong one for low-resource languages such as **Georgian**. |
| `diarize` | bool (default `true`) | Separate the speakers. **Turning it off loses per-speaker analysis** everywhere downstream. |
| `keyterms` | list of strings (default `[]`) | Bias recognition toward these words — product names, policy terms, place names. ≤ 1000 terms, each under 50 characters and at most 5 words; `< > { } [ ] \` are rejected. **Costs +20 %**, so it is opt-in. Supported by Scribe v2 models. |
| `audio_format` | `original` \| `flac_full` \| `flac_16k` \| `wav_16k` \| `mp3_16k` | What we encode your upload as before sending it: the original bytes untouched; lossless FLAC at the file's own rate; lossless FLAC at 16 kHz; WAV PCM s16le at 16 kHz; or lossy MP3 at 16 kHz. |

Manage the stored layer:

- `GET /transcription/config` — the **effective** settings for your workspace, plus `is_default`
  (`true` = you have set nothing of your own and are inheriting) and `inherited` (the layer
  underneath, so a UI can show the fallback), `override` (only the keys you set) and `formats`.
- `PUT /transcription/config` — save your override. Send only the fields you want to change; a
  field you omit goes back to being inherited. Requires owner authority (an owner login or the
  workspace API key).
- `DELETE /transcription/config` — drop the override entirely and inherit everything again.

Override for **one file** by adding a `transcription` form field — a JSON object with any subset
of the four keys — to any upload route (`/v1/transcriptions`, `/v1/analyze`, `/v1/analyses`,
`/v1/analyses/batch`). The uploads are `multipart/form-data`, so the object travels as a JSON
string in a form field:

```bash
curl -s -X POST $BASE/v1/transcriptions -H "X-API-Key: $KEY" \
  -F "file=@call.m4a" \
  -F 'transcription={"language_code":"ka","audio_format":"flac_16k","keyterms":["თვე","ლიმიტი"]}'
```

On `/v1/analyses/batch` the object applies to the whole batch. Settings are resolved when a
submission is **accepted**, so an async job is transcribed with what was in force when you sent
it, not with whatever changed while it queued.

Invalid settings are refused with **400** before anything is spent:
`{"detail": "keyterms: 'a<b>' contains < > …", "code": "invalid_transcription_setting",
"field": "keyterms"}` — `detail` always names the offending field.

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
JSON `{ "detail": "…" }`, sometimes with a sibling machine `code` (and, for transcription
settings, the offending `field`), with standard codes: `400` bad input, `401` missing/invalid key,
`404` not found / not yours, `413` too large, `429` rate-limited, `502` upstream (STT/LLM) error.
The analysis itself never partially fails silently — a failed job has `status:"error"` and an
`error` message.

## Limits & notes
- Single upload ≤ 100 MB; batch ≤ 50 files, ≤ 25 MB each.
- Audio is **not stored** after processing; results are retained. If the service restarts mid-job,
  that job is marked `error` — resubmit it (idempotency makes this safe).
- Everything is strictly tenant-isolated: you can only ever see your own KB, jobs, and rubric.

## Chat / bot API

The conversational surface — the public **autopilot** (`POST /v1/chat/answer`), the operator
**copilot** (`POST /v1/chat/turns` → `GET /v1/chat/suggestions/{suggest_ref}`, `POST /v1/chat/feedback`)
and the conversation **mirror** (`conversations:sync`, `DELETE /v1/chat/conversations/{external_ref}`) —
lives under `/v1/chat/` and is **not** reachable with the `X-API-Key` above. It uses a separate,
scoped **integration credential** (`X-CQ-Key: cqi_<key_id>.<secret>` + `X-CQ-Tenant`) that the CQ
superadmin issues to the chat service and grants per tenant; that credential, in turn, cannot
reach anything on this page. Contract, headers, envelope, status codes and the rollout checklist:
**[`docs/CHAT_INTEGRATION.md`](CHAT_INTEGRATION.md)**.
