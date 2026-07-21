"""Stage 2 of the curation loop: group the day's harvested failures into question clusters.

WHY this stage exists at all: cost. Stage 1 is pure SQL and yields hundreds of individual
failures, most of which are the *same* question asked by different people. Proposing on each
one would mean one Claude call per message — spend proportional to traffic volume, which is
exactly the shape a support product cannot afford. Clustering collapses those hundreds into
the ~6-10 distinct gaps a human reviewer can actually act on, using only embeddings (free:
self-hosted BGE-M3 on `cq-embeddings`). Everything expensive happens once per cluster, in
`propose.py`.

Three properties this file is responsible for:

* **Cross-lingual grouping for free.** BGE-M3 is multilingual, so the Georgian, Russian and
  English phrasings of "how long does a refund take?" land within cosine 0.82 of each other
  and become ONE cluster — and therefore one proposal, one review card, one KB document.
  No translation pass, no per-language pipeline. This is the main reason the embedding model
  is multilingual rather than English-only.
* **Semantic suppression, applied BEFORE any LLM call.** A declined card must cost tokens
  exactly once, not every night forever. Comparing a content hash would not survive contact
  with conversational traffic — the same question arrives with a typo, in another language,
  with different politeness framing, and every variant would be a fresh billable cluster.
  So the suppressed medoids are re-embedded in the SAME batch as the day's candidates and
  anything within cosine `SUPPRESS_THRESHOLD` of one is dropped here, upstream of spend.
* **Poisoning defence.** A cluster normally needs >= 2 distinct sources: one customer
  repeating a false premise ten times must not be able to write itself into a tenant's
  knowledge base. The exception is signals that are already human- or evidence-backed
  (S3 = an operator rewrote the AI's answer, S5/S6 = a fact-check verdict against the KB) —
  those are meaningful at n=1 and are exempted deliberately.

No numpy: the repo has no such dependency and this is a few hundred short vectors per tenant
per night, so plain-Python dot products over normalised lists are far below the noise floor
next to the HTTP round-trips to TEI.
"""
import hashlib
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field

# Imported as a MODULE, then called as `embeddings.embed_texts(...)` — the same shape
# `retrieval.py` and `kb_ingest.py` use. `from ..embeddings import embed_texts` binds the
# function object at import time, so anything that swaps the provider entry point later (the
# test suite's stub encoder; `invalidate_provider_cache`'s callers) would be invisible here and
# this module would silently keep calling whatever it captured first.
from .. import embeddings

log = logging.getLogger("cq")

# Cosine floor for two candidates to be the same question. 0.82 is the ADR's number and is a
# guess to be tuned against real traffic — too low merges distinct policies into one card,
# too high fragments one gap into three.
CLUSTER_THRESHOLD = 0.82

# Suppression is deliberately STRICTER than clustering: dropping a candidate silences it with
# no human in the loop, so it should require a closer match than merely grouping two questions
# that a reviewer will read side by side anyway.
SUPPRESS_THRESHOLD = 0.88

# Signals that are meaningful at n=1 (see module docstring). S3: an operator edited and sent a
# different answer — a human already wrote the right thing. S5/S6: a fact-check verdict against
# the tenant's own KB, which no customer can forge.
STRONG_SIGNALS = frozenset({"S3", "S5", "S6"})

# Precedence when a cluster mixes signals and the plain count ties: prefer the signal that
# carries the most evidence, because it is what the reviewer's card will be labelled with.
SIGNAL_PRECEDENCE = ("S3", "S6", "S5", "S2", "S1", "S4")

# TEI is CPU-bound here; a batch is one queue slot and one round-trip, not free compute. 32 is
# small enough that a slow batch cannot monopolise the encoder that live chat turns share.
EMBED_BATCH = 32

# Embeddings degrade badly on very long inputs and a mined "question" should be a question.
MAX_TEXT_CHARS = 512

# Hard ceiling on candidates considered in one run — the miner already caps, this is a second
# belt so a pathological day cannot turn into an O(n*k) blow-up in worker memory.
MAX_CANDIDATES = 2000


@dataclass
class Cluster:
    """One distinct question/gap, and every candidate that expressed it."""
    key: str                      # sha1 of the normalised medoid — stable across runs
    medoid: str                   # the most central member's text (the card headline source)
    candidates: list = field(default_factory=list)
    occurrences: int = 0          # how many failures this cluster covers
    distinct_sources: int = 0     # distinct conversations/calls — the poisoning defence
    signal: str = ""              # dominant harvest signal, S1..S6


# --- text normalisation -------------------------------------------------------

_WS = re.compile(r"\s+")
# Strip punctuation by Unicode category rather than an ASCII class: Georgian and Russian text
# carries punctuation that `string.punctuation` does not know about, and the whole point of the
# key is that two spellings of one question hash the same.
_PUNCT_CATS = {"P", "S"}


def normalise(text: str) -> str:
    """Casefold + NFKC + de-punctuate + collapse whitespace.

    Kept local rather than reusing `miner.normalise_question`: that one shapes text before it
    is *harvested* and may legitimately change as signals are tuned, whereas this one feeds a
    hash that must stay byte-stable forever — a change to it silently invalidates every stored
    `cluster_key`, un-suppressing every declined card at once.

    Used ONLY for the cluster key. Similarity is decided by the embedding, never by this —
    the key just needs to be deterministic for the same medoid across nights so that
    `curation_suppressions` and the partial unique index on open proposals line up.
    """
    text = unicodedata.normalize("NFKC", text or "").casefold()
    text = "".join(" " if unicodedata.category(ch)[0] in _PUNCT_CATS else ch for ch in text)
    return _WS.sub(" ", text).strip()


def cluster_key(medoid: str) -> str:
    return hashlib.sha1(normalise(medoid).encode("utf-8")).hexdigest()


def _text_of(cand) -> str:
    """What gets embedded: the question only.

    Deliberately NOT question+answer. The answer is what the proposal is *for*; folding it in
    would make two identical questions with differently-worded human answers look like two
    different gaps, which is precisely the fragmentation clustering exists to prevent.
    """
    return (getattr(cand, "question", "") or "").strip()[:MAX_TEXT_CHARS]


# --- vector helpers -----------------------------------------------------------

def _unit(vec: list[float]) -> list[float]:
    """Normalise defensively.

    TEI returns unit vectors for BGE-M3 today, but the provider is runtime-configurable
    (`app_settings.embeddings` can point at OpenAI instead), and `_cos` below is a bare dot
    product that would silently return garbage similarities against unnormalised input. One
    pass over ~1k short vectors is nothing; a wrong threshold everywhere is not.
    """
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0.0:
        return vec
    return [v / norm for v in vec]


def _cos(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two ALREADY-normalised vectors (see `_unit`)."""
    return sum(x * y for x, y in zip(a, b))


async def _embed_all(texts: list[str]) -> list[list[float]]:
    """Embed in batches of EMBED_BATCH via the ingest profile.

    `purpose="ingest"` and not "query": this is nightly batch work behind a worker, and the
    query profile's short timeout exists to protect an interactive chat turn that nobody is
    waiting on here.
    """
    out: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        chunk = texts[start:start + EMBED_BATCH]
        vecs = await embeddings.embed_texts(chunk, purpose="ingest")
        if len(vecs) != len(chunk):
            # Never silently mis-align a vector with the wrong candidate — that would cluster
            # unrelated questions together and put a stranger's quote on a review card.
            raise ValueError(
                f"embeddings returned {len(vecs)} vectors for {len(chunk)} texts")
        out.extend(_unit(list(v)) for v in vecs)
    return out


# --- aggregation --------------------------------------------------------------

def _source_id(cand) -> tuple:
    """Identity of the human/conversation behind a candidate, for the poisoning defence.

    Falls back through conversation -> source row -> object identity, so a candidate with no
    ids counts as its own source rather than collapsing every anonymous row into one.

    The conversation id lives in `Candidate.metadata`, NOT as an attribute — `miner.Candidate`
    has no `conversation_id` field. Reading only the attribute made every candidate its own
    source, so `distinct_sources` always equalled `occurrences` and this defence could never
    reject anything: one person repeating themselves three times looked like three conversations.
    """
    meta = getattr(cand, "metadata", None)
    conv = (meta or {}).get("conversation_id") if isinstance(meta, dict) else None
    conv = conv or getattr(cand, "conversation_id", None)
    if conv:
        return ("conversation", str(conv))
    sid = getattr(cand, "source_id", None)
    if sid:
        return (str(getattr(cand, "source_kind", "") or ""), str(sid))
    return ("object", id(cand))


def _dominant_signal(members: list) -> str:
    counts: dict[str, int] = {}
    for c in members:
        sig = str(getattr(c, "signal", "") or "")
        if sig:
            counts[sig] = counts.get(sig, 0) + 1
    if not counts:
        return ""
    best = max(counts.values())
    tied = [s for s, n in counts.items() if n == best]
    for sig in SIGNAL_PRECEDENCE:
        if sig in tied:
            return sig
    return sorted(tied)[0]


def _medoid_index(members: list[int], vecs: list[list[float]]) -> int:
    """The member with the highest mean similarity to the rest — the true medoid.

    The greedy pass seeds each cluster with whichever candidate happened to arrive first,
    which is an arbitrary representative. Re-centring matters because the medoid is what the
    reviewer reads as the headline, what `propose.py` retrieves against, and what next
    night's suppression compares to.
    """
    if len(members) == 1:
        return members[0]
    best_idx, best_score = members[0], -2.0
    for i in members:
        score = sum(_cos(vecs[i], vecs[j]) for j in members if j != i) / (len(members) - 1)
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx


async def cluster(cands: list, suppressed_medoids: list[str],
                  threshold: float = CLUSTER_THRESHOLD) -> list[Cluster]:
    """Group candidates into question clusters, dropping suppressed and thin ones.

    `suppressed_medoids` are the medoids of still-active `curation_suppressions` rows for this
    tenant. They are embedded in the SAME batch as the candidates — one round-trip, and no way
    for the two sets to be compared under different provider configs.

    Returns clusters ordered by occurrences (desc), each already carrying the counts the
    proposal row needs. Never raises for an empty input; an embedding failure DOES propagate,
    because a run that silently produced zero clusters would look identical to a quiet night.
    """
    cands = [c for c in (cands or []) if _text_of(c)]
    if not cands:
        return []
    if len(cands) > MAX_CANDIDATES:
        log.warning("curation cluster: capping %s candidates to %s", len(cands), MAX_CANDIDATES)
        cands = cands[:MAX_CANDIDATES]

    supp = [s.strip()[:MAX_TEXT_CHARS] for s in (suppressed_medoids or []) if (s or "").strip()]
    texts = supp + [_text_of(c) for c in cands]
    vecs = await _embed_all(texts)
    supp_vecs, cand_vecs = vecs[:len(supp)], vecs[len(supp):]

    # 1. Semantic suppression — before any Claude call, which is the whole point.
    live: list[int] = []
    dropped = 0
    for i in range(len(cands)):
        if any(_cos(cand_vecs[i], sv) >= SUPPRESS_THRESHOLD for sv in supp_vecs):
            dropped += 1
            continue
        live.append(i)
    if dropped:
        log.info("curation cluster: suppressed %s/%s candidates", dropped, len(cands))
    if not live:
        return []

    # 2. Greedy assignment against the current seed of each cluster. O(n*k) with k = number of
    #    distinct questions, which is small by construction — that is what makes greedy fine
    #    here where a full pairwise matrix would not be.
    groups: list[list[int]] = []
    seeds: list[int] = []
    for i in live:
        placed = False
        for g, seed in enumerate(seeds):
            if _cos(cand_vecs[i], cand_vecs[seed]) >= threshold:
                groups[g].append(i)
                placed = True
                break
        if not placed:
            seeds.append(i)
            groups.append([i])

    # 3. Re-centre, count, and apply the poisoning defence.
    out: list[Cluster] = []
    for members in groups:
        med_i = _medoid_index(members, cand_vecs)
        picked = [cands[i] for i in members]
        sources = {_source_id(c) for c in picked}
        signal = _dominant_signal(picked)
        strong = any(str(getattr(c, "signal", "") or "") in STRONG_SIGNALS for c in picked)
        if len(sources) < 2 and not strong:
            # One person, one weak signal. Could be a genuine gap; could be someone trying to
            # get a false statement approved into the KB. It waits for a second source.
            continue
        medoid = _text_of(cands[med_i])
        out.append(Cluster(
            key=cluster_key(medoid),
            medoid=medoid,
            candidates=picked,
            occurrences=len(picked),
            distinct_sources=len(sources),
            signal=signal,
        ))

    out.sort(key=lambda c: (c.occurrences, c.distinct_sources), reverse=True)
    return out
