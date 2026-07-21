#!/usr/bin/env bash
# Measure the query-embedding latency of the TEI (BGE-M3) container — the ruler for the
# whole conversational-AI latency promise.
#
# WHY THIS EXISTS
# The cold read path is: auth -> query build -> EMBED -> pgvector x2 + RRF -> gate ->
# tier-1 cards on screen. Every term but EMBED is single-digit-to-low-tens of ms, so the
# embed forward pass is the budget. docker-compose.yml pins the fp32 CPU TEI image with
# no --dtype, no ONNX/int8 flag and no CPU reservation, i.e. XLM-RoBERTa-large-shaped
# inference (~24 GFLOPs for a 40-token query) on shared VPS cores — and the repo has no
# instrumentation on the embed path at all. Every published latency number for this
# feature is an assumption until this script has been run.
#
# THE THRESHOLD AND ITS CONSEQUENCE (ADR-001)
#   p95 > 150 ms  ->  swapping TEI for an ONNX/int8 image becomes P0 SCOPE, not a future
#                     escalation. No amount of application code recovers a slow forward
#                     pass; caching, keep-alive clients and batching move the small term
#                     only. Do not ship the P1 latency promise on an unmeasured p95.
#   p95 <= 150 ms ->  the fp32 image stands; record the number in the ADR, replacing the
#                     placeholder estimate.
#
# WHERE TO RUN IT
# ON THE SERVER, against the live stack (`docker compose -p cqv3 exec`). A laptop number
# is meaningless here: the whole question is what these shared VPS cores do under the
# real container's CPU share.
#
#   ssh <server>            # needs the CQ VPN — see docs/DEPLOYMENT.local.md
#   cd /home/cqdeploy/cq-v3-ai
#   ./deploy/measure-embed-latency.sh                 # idle baseline
#   ./deploy/measure-embed-latency.sh --under-load    # with KB-import traffic in flight
#
# The --under-load run is the one that decides the product promise: a chat query never
# arrives on a quiet box, it arrives while somebody is importing a knowledge base into
# the SAME single-replica TEI container. Contention, not the idle number, is the risk.

set -euo pipefail

PROJECT=cqv3
SERVICE=embeddings
SAMPLES=${SAMPLES:-30}          # measured requests (after the cold one)
LOAD_BATCH=${LOAD_BATCH:-32}    # chunks per background batch — matches the ingest batch size
MODE=${1:-idle}

# ~40 tokens of Georgian: a realistic customer-support question, not lorem ipsum. Token
# count matters (the forward pass is roughly linear in it) and so does the script —
# Georgian fragments into far more BGE-M3 tokens per word than English does.
QUERY='გამარჯობა, მაინტერესებს თუ შემიძლია ჩემი სადებეტო ბარათის ლიმიტის გაზრდა ონლაინ განაცხადით, რა დოკუმენტები მჭირდება ამისთვის და რამდენი სამუშაო დღე სჭირდება განხილვას, ასევე აქვს თუ არა საკომისიო'

if [[ "$MODE" != "idle" && "$MODE" != "--under-load" ]]; then
    echo "usage: $0 [--under-load]" >&2
    exit 2
fi

# Built once, outside the loop: json.dumps keeps the Georgian bytes intact and correctly
# escaped, which a hand-rolled shell heredoc would not guarantee.
QUERY_PAYLOAD=$(printf '%s' "$QUERY" | python3 -c \
    'import json,sys; print(json.dumps({"inputs": sys.stdin.read()}))')

# curl is measured INSIDE the container so the number is the model's forward pass plus
# TEI's own overhead, with no docker-network or host-loopback term mixed in. The api
# container adds its own hop on top of this; that hop is small and already accounted for.
embed_once() {
    printf '%s' "$QUERY_PAYLOAD" | docker compose -p "$PROJECT" exec -T "$SERVICE" \
        curl -s -o /dev/null -w '%{time_total}\n' \
             -X POST http://localhost:80/embed \
             -H 'Content-Type: application/json' --data-binary @-
}

# Reproduces KB-import contention: repeated 32-text batches at the same TEI container,
# which is exactly what services/kb_ingest.py does while a document is being embedded.
start_background_load() {
    local payload
    payload=$(python3 - "$LOAD_BATCH" <<'PY'
import json, sys
n = int(sys.argv[1])
chunk = ("მომხმარებელმა მოითხოვა ინფორმაცია ანგარიშის მომსახურების პირობებზე და "
         "საკომისიოებზე, ოპერატორმა უპასუხა ბანკის მოქმედი ტარიფების მიხედვით. ") * 4
print(json.dumps({"inputs": [chunk] * n}))
PY
)
    (
        while :; do
            printf '%s' "$payload" | docker compose -p "$PROJECT" exec -T "$SERVICE" \
                curl -s -o /dev/null -X POST http://localhost:80/embed \
                     -H 'Content-Type: application/json' --data-binary @- || true
        done
    ) &
    LOAD_PID=$!
    trap 'kill "$LOAD_PID" 2>/dev/null || true' EXIT
    # Let the first batch actually get onto the CPU before we start sampling.
    sleep 3
}

echo "== embed latency: mode=$MODE samples=$SAMPLES =="
docker compose -p "$PROJECT" ps "$SERVICE"

if [[ "$MODE" == "--under-load" ]]; then
    echo "-- starting background KB-import load (${LOAD_BATCH}-text batches, continuous)"
    start_background_load
fi

# "Cold" here means first-request-after-idle: kernels warmed but nothing cached. A true
# process-cold number needs `docker compose -p cqv3 restart embeddings` first, which is
# disruptive — do that deliberately, not as part of every run.
COLD=$(embed_once)
echo "cold (first request): ${COLD}s"

TIMES=$(for _ in $(seq 1 "$SAMPLES"); do embed_once; done)

printf '%s\n' "$TIMES" | sort -n | awk -v mode="$MODE" '
    function pct(p,   i) { i = int(NR * p + 0.9999); if (i < 1) i = 1; if (i > NR) i = NR; return t[i] }
    { t[NR] = $1 * 1000 }
    END {
        if (NR == 0) { print "no samples"; exit 1 }
        p50 = pct(0.50); p95 = pct(0.95)
        printf "n=%d  min=%.1fms  p50=%.1fms  p95=%.1fms  max=%.1fms  (%s)\n",
               NR, t[1], p50, p95, t[NR], mode
        if (p95 > 150) {
            print "VERDICT: p95 > 150ms -> ONNX/int8 TEI image is P0 SCOPE (ADR-001)."
        } else {
            print "VERDICT: p95 <= 150ms -> fp32 TEI stands; record this number in ADR-001."
        }
    }'
