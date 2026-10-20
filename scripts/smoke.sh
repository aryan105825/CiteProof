#!/usr/bin/env bash
# @exports: smoke.sh -> exit 0 if all 5 upstream API checks pass, non-zero on first failure
# @imports: none
# @env: CITEPROOF_MAILTO
# @schema: checks Crossref select, Crossref query.bibliographic, Crossref DOI lookup, OpenAlex search, OpenAlex DOI lookup
set -u
set -o pipefail

MAILTO="${CITEPROOF_MAILTO:-}"
if [ -z "$MAILTO" ]; then
  echo "WARN: CITEPROOF_MAILTO unset; using anonymous@example.org. Set it in .env for the Crossref polite pool." >&2
  MAILTO="anonymous@example.org"
fi
UA="CiteProof/0.1 (mailto:${MAILTO})"
REF="LeCun, Bengio, Hinton. Deep learning. Nature 521, 436-444 (2015)"
DOI="10.1038/nature14539"

FAILED=0

# check <label> <expected-regex> <curl args...>
check() {
  local label="$1" pattern="$2"
  shift 2
  local body
  if ! body="$(curl -sS -f --max-time 20 -A "$UA" "$@" 2>&1)"; then
    echo "FAIL $label: request failed: $body" >&2
    echo "  fix: check your network, and that api.crossref.org and api.openalex.org are reachable." >&2
    FAILED=1
    return
  fi
  if printf '%s' "$body" | grep -qiE "$pattern"; then
    echo "PASS $label"
  else
    echo "FAIL $label: response did not match /$pattern/. First 200 chars: $(printf '%s' "$body" | head -c 200)" >&2
    echo "  fix: the API response shape may have changed; update citeproof parsers accordingly." >&2
    FAILED=1
  fi
  sleep 1
}

check "crossref select" '"DOI"' \
  --get "https://api.crossref.org/works" \
  --data-urlencode "rows=1" \
  --data-urlencode "select=DOI,title" \
  --data-urlencode "mailto=${MAILTO}"

check "crossref query.bibliographic" '"title"' \
  --get "https://api.crossref.org/works" \
  --data-urlencode "query.bibliographic=${REF}" \
  --data-urlencode "rows=5" \
  --data-urlencode "select=DOI,title,author,issued,container-title,score,URL,volume,page" \
  --data-urlencode "mailto=${MAILTO}"

check "crossref doi lookup" 'nature14539' \
  "https://api.crossref.org/works/${DOI}?mailto=${MAILTO}"

check "openalex search" '"results"' \
  --get "https://api.openalex.org/works" \
  --data-urlencode "search=${REF}" \
  --data-urlencode "per_page=5" \
  --data-urlencode "api_key=${OPENALEX_API_KEY}" \
  --data-urlencode "mailto=${MAILTO}"

check "openalex doi lookup" '"display_name"' \
  "https://api.openalex.org/works/https://doi.org/${DOI}?mailto=${MAILTO}&api_key=${OPENALEX_API_KEY}"

if [ "$FAILED" -ne 0 ]; then
  echo "SMOKE FAILED: fix the failures above before starting M1." >&2
  exit 1
fi
echo "SMOKE OK"
