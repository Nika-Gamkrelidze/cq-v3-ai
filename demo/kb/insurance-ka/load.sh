#!/usr/bin/env bash
# Load the "ალიონი დაზღვევა" demo knowledge base into one CQ workspace.
#
#   CQ_URL=https://ai.communiq.ge/api CQ_TENANT_API_KEY=<tenant api key> ./load.sh
#   CQ_URL=https://ai.communiq.ge/api CQ_ADMIN_TOKEN=<superadmin token> CQ_TENANT=<slug|uuid> ./load.sh
#
# Idempotent by title: a document whose title already exists in the workspace is skipped, so
# re-running after a partial load only adds what is missing. Customer-facing documents are
# shared with the bot (visibility=public); the internal sales script stays internal on purpose.
set -euo pipefail
cd "$(dirname "$0")"
: "${CQ_URL:=https://ai.communiq.ge/api}"
CQ_URL="${CQ_URL%/}"

if [[ -n "${CQ_TENANT_API_KEY:-}" ]]; then
  AUTH=(-H "X-API-Key: $CQ_TENANT_API_KEY")
elif [[ -n "${CQ_ADMIN_TOKEN:-}" && -n "${CQ_TENANT:-}" ]]; then
  # Operator scope: a superadmin acting as one workspace, through the same /kb routes.
  AUTH=(-H "X-Admin-Token: $CQ_ADMIN_TOKEN" -H "X-Act-As-Tenant: $CQ_TENANT")
else
  echo "Set CQ_TENANT_API_KEY, or CQ_ADMIN_TOKEN and CQ_TENANT (slug or uuid)." >&2; exit 2
fi

# file | title | doc_type | tags | visibility
DOCS=$(cat <<'TABLE'
01-kompania.md|ალიონი დაზღვევა — კომპანია და კონტაქტები|company|კომპანია,კონტაქტი|public
02-avto-dazgveva.md|ავტოდაზღვევა — ავტოპასუხისმგებლობა და კასკო|policy|ავტო,კასკო|public
03-janmrtelobis-dazgveva.md|ჯანმრთელობის დაზღვევა — სტანდარტი, პრემიუმი, ოჯახური|policy|ჯანმრთელობა|public
04-qonebis-dazgveva.md|ქონების დაზღვევა — ბინა და სახლი|policy|ქონება|public
05-samogzauro-dazgveva.md|სამოგზაურო დაზღვევა|policy|სამოგზაურო|public
06-ubeduri-shemtxveva.md|უბედური შემთხვევის დაზღვევა|policy|უბედური შემთხვევა|public
07-zaralis-anazgaureba.md|ზარალის ანაზღაურება — წესი და ვადები|procedure|ზარალი|public
08-gadaxda-da-polisi.md|გადახდა და პოლისის ადმინისტრირება|procedure|გადახდა,პოლისი|public
09-terminebi.md|სადაზღვევო ტერმინების განმარტება|glossary|ტერმინები|public
10-faq.csv|ხშირად დასმული კითხვები|faq|faq|public
90-shida-instruqcia.md|შიდა ინსტრუქცია გაყიდვების აგენტებისთვის (კონფიდენციალური)|internal|შიდა|internal
TABLE
)

json() { python3 -c "import sys,json; d=json.load(sys.stdin); $1"; }

echo "→ $CQ_URL"
existing=$(curl -sf "$CQ_URL/kb/documents?limit=500" "${AUTH[@]}" \
  | json 'items = d if isinstance(d,list) else (d.get("items") or d.get("documents") or d.get("results") or []); print("\n".join(x.get("title","") for x in items))')

public_ids=(); created=0; skipped=0; failed=0
while IFS='|' read -r file title doc_type tags vis; do
  [[ -z "$file" ]] && continue
  if grep -Fxq -- "$title" <<<"$existing"; then
    echo "  = skip (exists): $title"; skipped=$((skipped+1)); continue
  fi
  if [[ "$file" == *.csv ]]; then ep="documents/csv"; else ep="documents/upload"; fi
  resp=$(curl -s -w '\n%{http_code}' -X POST "$CQ_URL/kb/$ep" "${AUTH[@]}" \
           -F "file=@$file" -F "title=$title" -F "doc_type=$doc_type" -F "tags=$tags")
  code=${resp##*$'\n'}; body=${resp%$'\n'*}
  if [[ "$code" != "200" && "$code" != "201" ]]; then
    echo "  ✗ $title → HTTP $code: $body"; failed=$((failed+1)); continue
  fi
  id=$(json 'print(d["id"])' <<<"$body")
  echo "  + $title ($id)"; created=$((created+1))
  [[ "$vis" == "public" ]] && public_ids+=("$id")
  # Wait for embedding before moving on: the server processes one document at a time per
  # request, and a public toggle on a document that is still 'pending' is fine but confusing.
  for _ in $(seq 1 60); do
    st=$(curl -sf "$CQ_URL/kb/documents/$id" "${AUTH[@]}" | json 'print(d.get("status",""))')
    case "$st" in ready) echo "      ready"; break;; error) echo "      ✗ ingest error"; failed=$((failed+1)); break;; esac
    sleep 3
  done
done <<<"$DOCS"

if (( ${#public_ids[@]} )); then
  ids=$(printf '"%s",' "${public_ids[@]}"); ids="[${ids%,}]"
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$CQ_URL/kb/documents/visibility" "${AUTH[@]}" \
           -H 'Content-Type: application/json' -d "{\"document_ids\":$ids,\"visibility\":\"public\"}")
  echo "→ shared ${#public_ids[@]} document(s) with the bot (HTTP $code)"
fi

echo "→ done: $created created, $skipped skipped, $failed failed"
echo "→ now in the portal: BOT tab → write the refusal copy → enable autopilot."
