#!/usr/bin/env bash
# @exports: run_all.sh -> preflight, wild-set readiness, then scripts/verify.sh; exits 2 (STOP) when something must be fixed first, else verify.sh's exit code
# @imports: scripts/verify.sh, data/wild_labeled.csv, data/wild_labeled_second.csv
# @env: CITEPROOF_MAILTO
# @schema: loads .env if CITEPROOF_MAILTO is unset; sets chatbot names from id prefixes gmf-/gmp-/gpt- (idempotent); prints a DOI click-list for empty labels; writes data/second_sheet.csv (30 blind rows) when second labels are missing
set -u
cd "$(dirname "$0")/.." || { echo "STOP: cannot cd to the repo root" >&2; exit 2; }
stop() { echo "STOP: $1" >&2; exit 2; }

if [ -z "${CITEPROOF_MAILTO:-}" ] && [ -f .env ]; then set -a; . ./.env; set +a; fi
[ -n "${CITEPROOF_MAILTO:-}" ] || stop "CITEPROOF_MAILTO is not set. Run: cp .env.example .env, put your email in it, then re-run."

python3 -c "import sys; sys.exit(0 if (3, 12) <= sys.version_info[:2] < (3, 14) else 1)" \
  || stop "Python 3.12 or 3.13 is required (found $(python3 -V 2>&1)). Recreate the venv with python3.12."
python3 -c "import requests, rapidfuzz, pypdf, fastapi, uvicorn" 2>/dev/null \
  || stop "Dependencies are missing. Run: pip install -r requirements.txt"
git rev-parse HEAD >/dev/null 2>&1 \
  || stop "Not a git repo with a commit yet. Run: git init && git add -A && git commit -m start"
git ls-files --error-unmatch CLAIMS.md >/dev/null 2>&1 \
  || stop "CLAIMS.md is not committed. Run: git add -A && git commit -m 'pre-register claims' BEFORE the benchmark."

python3 - <<'PY' || exit 2
import csv, re, sys
path = "data/wild_labeled.csv"
rows = list(csv.DictReader(open(path, encoding="utf-8", newline="")))
if not rows:
    sys.exit("STOP: data/wild_labeled.csv has no rows. Build the wild set first (see README).")
cols = list(rows[0].keys())
names = {"gmf-": "Gemini Flash", "gmp-": "Gemini Pro", "gpt-": "ChatGPT"}
for r in rows:
    for prefix, name in names.items():
        if r["id"].startswith(prefix):
            r["chatbot"] = name
with open(path, "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)

problems = []
if len(rows) < 100:
    problems.append("only %d rows (need 100+)" % len(rows))
if len({r["chatbot"] for r in rows}) < 3:
    problems.append("fewer than 3 distinct chatbots")
if len({r["prompt_topic"] for r in rows}) < 12:
    problems.append("fewer than 12 distinct topics")
empty = [r for r in rows if not r["label"].strip()]
if empty:
    problems.append("%d rows have no label; open each DOI, compare, then edit data/wild_labeled.csv" % len(empty))
    for r in empty:
        m = re.search(r"10\.\d{4,9}/\S+", r["raw"])
        link = "https://doi.org/" + m.group(0).rstrip(".,;)") if m else "(no DOI in the reference: search Crossref by title)"
        print("  %s  %s" % (r["id"], link), file=sys.stderr)
if problems:
    sys.exit("STOP: wild set not ready: " + "; ".join(problems))

try:
    second = list(csv.DictReader(open("data/wild_labeled_second.csv", encoding="utf-8", newline="")))
except FileNotFoundError:
    second = []
if len(second) < 25:
    import random
    with open("data/second_sheet.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["id", "raw", "label"])
        shuffled = list(rows); random.Random(1).shuffle(shuffled)
        for r in shuffled[:30]:
            w.writerow([r["id"], r["raw"], ""])
    sys.exit("STOP: second labeller missing (%d rows, need 25+). Wrote data/second_sheet.csv with 30 blind rows: "
             "send it to a human, then save their id,label answers to data/wild_labeled_second.csv." % len(second))
print("wild set ready: %d rows, %d chatbots, %d second labels" % (len(rows), len({r['chatbot'] for r in rows}), len(second)))
PY

bash scripts/verify.sh
code=$?
echo
if [ -f results/summary.md ]; then
  echo "--- results/summary.md (paste into README.md, then commit results AFTER CLAIMS.md) ---"
  cat results/summary.md
fi
exit "$code"
