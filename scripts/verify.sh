#!/usr/bin/env bash
# @exports: verify.sh -> prints "PASS Cn <claim>" or "FAIL Cn <claim>: <reason>"; exit 0 all pass, 1 any FAIL, 3 for a VERIFY_ONLY filtered run
# @imports: scripts/smoke.sh, citeproof.cli, citeproof.api, bench.build_dataset, bench.run_benchmark, CLAIMS.md (TARGET lines), data/wild_labeled.csv
# @env: CITEPROOF_MAILTO
# @env: VERIFY_ONLY
# @env: CITEPROOF_CACHE
# @schema: forces CITEPROOF_CACHE=0; writes results/*.json via bench.run_benchmark and results/summary.md; VERIFY_ONLY=C1,C2 runs a subset (dev only) and exits 3 with "FILTERED RUN"
set -u
cd "$(dirname "$0")/.." || { echo "FAIL setup: cannot cd to the repo root" >&2; exit 2; }
export CITEPROOF_CACHE=0
FAILS=0
ONLY="${VERIFY_ONLY:-}"

want() {
  [ -z "$ONLY" ] && return 0
  case ",$ONLY," in *",$1,"*) return 0 ;; esac
  return 1
}

PYLIB=$(cat <<'PY'
import json, os, subprocess, sys

def cli(ref, mode="evidence"):
    p = subprocess.run([sys.executable, "-m", "citeproof.cli", "check", ref, "--mode", mode, "--json"],
                       capture_output=True, text=True)
    if p.returncode not in (0, 1):
        raise SystemExit("cli exit %d: %s" % (p.returncode, p.stderr.strip()[-300:]))
    return json.loads(p.stdout)["items"][0]

def need_bench():
    if os.environ.get("BENCH_ERR"):
        raise SystemExit("benchmark did not complete: " + os.environ["BENCH_ERR"])

def targets():
    t = {}
    for line in open("CLAIMS.md", encoding="utf-8"):
        parts = line.strip().split()
        if len(parts) == 3 and parts[0] == "TARGET":
            t[parts[1]] = float(parts[2])
    for k in ("synthetic_fpr_max", "synthetic_f1_min", "synthetic_recall_corrupted_margin", "wild_fpr_max", "wild_f1_min"):
        if k not in t:
            raise SystemExit("CLAIMS.md is missing TARGET " + k)
    return t
PY
)

# check <id> <claim>   (python code on stdin; a non-zero exit is a FAIL and its message is the reason)
check() {
  local id="$1" claim="$2" code out rc
  code="${PYLIB}"$'\n'"$(cat)"
  want "$id" || return 0
  out="$(python3 -c "$code" 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "PASS $id $claim"
  else
    echo "FAIL $id $claim: $out"
    FAILS=$((FAILS + 1))
  fi
}

# C1: live upstream APIs
if want C1; then
  smoke_out="$(bash scripts/smoke.sh 2>&1)"
  if [ $? -eq 0 ]; then
    echo "PASS C1 Crossref and OpenAlex answer all five smoke requests"
  else
    echo "FAIL C1 Crossref and OpenAlex answer all five smoke requests: $(printf '%s' "$smoke_out" | tail -n 3 | tr '\n' ' ')"
    FAILS=$((FAILS + 1))
  fi
fi

# Benchmark (frozen dataset is built only if missing)
BENCH_ERR=""
bench_wanted=1
if [ -n "$ONLY" ]; then
  bench_wanted=0
  for c in C8 C9 C10 C11 C12 C14; do want "$c" && bench_wanted=1; done
fi
if [ "$bench_wanted" -eq 1 ]; then
  mkdir -p results
  if [ ! -f data/bench_records.json ]; then
    if ! python3 -m bench.build_dataset --n 400 --seed 7 --out data/bench_records.json 2>results/build.err; then
      BENCH_ERR="build_dataset failed: $(tail -n 3 results/build.err | tr '\n' ' ')"
    fi
  fi
  if [ -z "$BENCH_ERR" ]; then
    if ! python3 -m bench.run_benchmark --records data/bench_records.json --wild data/wild_labeled.csv --out results --workers 3 2>results/bench.err; then
      BENCH_ERR="run_benchmark failed: $(grep -E '^ERROR' results/bench.err | tail -n 1)"
    fi
  fi
fi
export BENCH_ERR

check C2 "LeCun reference is VERIFIED in strong mode with DOI 10.1038/nature14539 (live)" <<'PY'
it = cli("LeCun, Bengio, Hinton. Deep learning. Nature 521, 436-444 (2015)", "strong")
doi = (it["best"] or {}).get("doi")
if it["label"] != "VERIFIED" or doi != "10.1038/nature14539":
    raise SystemExit("label=%s doi=%s reasons=%s" % (it["label"], doi, it["reasons"]))
PY

check C3 "LeCun title and authors with year 2011 and venue Science is MISMATCH" <<'PY'
it = cli("LeCun, Bengio, Hinton. Deep learning. Science 521, 436-444 (2011)")
if it["label"] != "MISMATCH":
    raise SystemExit("label=%s reasons=%s" % (it["label"], it["reasons"]))
PY

check C4 "A DOI belonging to another paper gives DOI_POINTS_ELSEWHERE" <<'PY'
it = cli("LeCun, Y., Bengio, Y., & Hinton, G. (2015). Deep learning. Nature, 521, 436-444. https://doi.org/10.1145/3065386")
if "DOI_POINTS_ELSEWHERE" not in it["reasons"]:
    raise SystemExit("label=%s reasons=%s" % (it["label"], it["reasons"]))
PY

check C5 "A genuine CVPR-abbreviated reference stays VERIFIED" <<'PY'
it = cli("He, K., Zhang, X., Ren, S., & Sun, J. (2016). Deep residual learning for image recognition. In CVPR (pp. 770-778).")
if it["label"] != "VERIFIED":
    raise SystemExit("label=%s reasons=%s evidence=%s" % (it["label"], it["reasons"], it["evidence"]))
PY

check C6 "extract finds 3 references in a generated single-column PDF" <<'PY'
import tempfile
lines = [
    "References",
    "[1] LeCun, Y., Bengio, Y., Hinton, G. Deep learning. Nature 521, 436-444 (2015).",
    "[2] He, K., Zhang, X., Ren, S., Sun, J. Deep residual learning for image recognition. CVPR 2016, pp. 770-778.",
    "[3] Vaswani, A., Shazeer, N., Parmar, N. Attention is all you need. NeurIPS 2017, pp. 5998-6008.",
]
def esc(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
stream = "BT /F1 9 Tf 50 740 Td 14 TL\n" + "".join("(%s) Tj T*\n" % esc(l) for l in lines) + "ET"
objs = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    "<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
]
out = b"%PDF-1.4\n"
offs = []
for i, o in enumerate(objs, 1):
    offs.append(len(out))
    out += ("%d 0 obj\n%s\nendobj\n" % (i, o)).encode("latin-1")
xref = len(out)
out += ("xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)).encode()
for off in offs:
    out += ("%010d 00000 n \n" % off).encode()
out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)).encode()
with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
    f.write(out)
    path = f.name
try:
    p = subprocess.run([sys.executable, "-m", "citeproof.cli", "extract", path], capture_output=True, text=True)
finally:
    os.remove(path)
if p.returncode != 0:
    raise SystemExit("extract exit %d: %s" % (p.returncode, p.stderr.strip()[-300:]))
last = p.stdout.strip().splitlines()[-1]
if last != "3 references":
    raise SystemExit("expected '3 references', got %r; output: %s" % (last, p.stdout[-400:]))
PY

check C7 "API serves the page, health, and weak/strong/evidence reports with a corrected reference" <<'PY'
import time, urllib.error, urllib.request
port = "8765"
logpath = "results/uvicorn.log"
os.makedirs("results", exist_ok=True)
log = open(logpath, "w")
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "citeproof.api:app", "--port", port], stdout=log, stderr=log)
try:
    base = "http://127.0.0.1:" + port
    health = None
    for _ in range(80):
        if srv.poll() is not None:
            raise SystemExit("uvicorn exited early: " + open(logpath).read()[-300:])
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=30) as r:
                health = json.load(r)
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)  # server still starting; retry until the loop ends
    if health is None:
        raise SystemExit("server did not come up within 40s")
    if health.get("ok") is not True or health.get("crossref_reachable") is not True:
        raise SystemExit("health=%s" % health)
    body = json.dumps({"text": "LeCun, Bengio, Hinton. Deep learning. Science 521, 436-444 (2011)", "compare": True}).encode()
    req = urllib.request.Request(base + "/api/check", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r)
    for m in ("weak", "strong", "evidence"):
        if m not in d:
            raise SystemExit("missing %s report; keys=%s" % (m, list(d)))
    ev = d["evidence"]["items"][0]
    if ev["label"] != "MISMATCH" or not ev["suggested"]:
        raise SystemExit("evidence label=%s suggested=%r" % (ev["label"], ev["suggested"]))
    with urllib.request.urlopen(base + "/", timeout=30) as r:
        if r.status != 200 or b"CiteProof" not in r.read():
            raise SystemExit("GET / did not return the CiteProof page")
finally:
    srv.terminate()
    srv.wait(timeout=15)
    log.close()
PY

check C8 "Synthetic test targets are met (measured)" <<'PY'
need_bench()
t = targets()
s = json.load(open("results/bench_synthetic.json"))
e, w = s["evidence"], s["baseline_strong"]
bad = []
if e["fpr"] > t["synthetic_fpr_max"]:
    bad.append("FPR %.3f > %.2f" % (e["fpr"], t["synthetic_fpr_max"]))
if e["f1"] < t["synthetic_f1_min"]:
    bad.append("F1 %.3f < %.2f" % (e["f1"], t["synthetic_f1_min"]))
if e["recall_corrupted"] < w["recall_corrupted"] + t["synthetic_recall_corrupted_margin"]:
    bad.append("recall_corrupted %.3f < strong %.3f + %.2f" % (e["recall_corrupted"], w["recall_corrupted"], t["synthetic_recall_corrupted_margin"]))
if bad:
    raise SystemExit("; ".join(bad))
PY

check C9 "Wild targets are met (measured)" <<'PY'
need_bench()
t = targets()
s = json.load(open("results/bench_wild.json"))
e, w = s["evidence"], s["baseline_strong"]
bad = []
if e["fpr"] > t["wild_fpr_max"]:
    bad.append("FPR %.3f > %.2f" % (e["fpr"], t["wild_fpr_max"]))
if e["f1"] < t["wild_f1_min"]:
    bad.append("F1 %.3f < %.2f" % (e["f1"], t["wild_f1_min"]))
if not e["recall"] > w["recall"]:
    bad.append("recall %.3f is not above strong %.3f" % (e["recall"], w["recall"]))
if bad:
    raise SystemExit("; ".join(bad))
PY

check C10 "Wild set has >=100 rows, >=3 chatbots, >=12 topics and second-labeller agreement on >=25 rows" <<'PY'
need_bench()
import csv
rows = list(csv.DictReader(open("data/wild_labeled.csv", encoding="utf-8", newline="")))
st = json.load(open("results/wild_stats.json"))
bad = []
if len(rows) < 100 or st["n"] != len(rows):
    bad.append("rows=%d stats.n=%d" % (len(rows), st["n"]))
if len(st["by_chatbot"]) < 3:
    bad.append("chatbots=%d" % len(st["by_chatbot"]))
if len({r["prompt_topic"] for r in rows}) < 12:
    bad.append("topics=%d" % len({r["prompt_topic"] for r in rows}))
if st["labeller_agreement"]["n"] < 25:
    bad.append("second-labeller n=%d" % st["labeller_agreement"]["n"])
if bad:
    raise SystemExit("; ".join(bad))
PY

check C11 "error_analysis.json lists up to 5 real failure cases" <<'PY'
need_bench()
d = json.load(open("results/error_analysis.json"))
if len(d["cases"]) != min(5, d["n_errors_total"]):
    raise SystemExit("cases=%d n_errors_total=%d" % (len(d["cases"]), d["n_errors_total"]))
PY

check C12 "README results table contains the measured evidence-mode numbers" <<'PY'
need_bench()
syn = json.load(open("results/bench_synthetic.json"))["evidence"]
wild = json.load(open("results/bench_wild.json"))["evidence"]
st = json.load(open("results/wild_stats.json"))
rows = ["| Set | Precision | Recall | FPR | F1 |", "|---|---|---|---|---|"]
for name, e in (("Synthetic test", syn), ("Wild (hand-labelled)", wild)):
    rows.append("| %s | %.3f | %.3f | %.3f | %.3f |" % (name, e["precision"], e["recall"], e["fpr"], e["f1"]))
rows.append("")
rows.append("Wild bad-reference fraction: %.3f (n=%d); second-labeller kappa %.3f (n=%d)." % (
    st["bad_fraction"], st["n"], st["labeller_agreement"]["cohen_kappa"], st["labeller_agreement"]["n"]))
open("results/summary.md", "w", encoding="utf-8").write("\n".join(rows) + "\n")
readme = open("README.md", encoding="utf-8").read()
missing = []
for name, e in (("synthetic", syn), ("wild", wild)):
    for k in ("recall", "fpr", "f1"):
        if "%.3f" % e[k] not in readme:
            missing.append("%s %s=%.3f" % (name, k, e[k]))
if missing:
    raise SystemExit("README lacks measured numbers (%s); paste results/summary.md into it" % ", ".join(missing))
PY

check C13 "CLAIMS.md was committed no later than the first commit of results/bench_synthetic.json" <<'PY'
def first(path):
    p = subprocess.run(["git", "log", "--diff-filter=A", "--follow", "--format=%ct", "--", path], capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit("git log failed: " + p.stderr.strip()[-200:])
    parts = p.stdout.split()
    return int(parts[-1]) if parts else None
c, r = first("CLAIMS.md"), first("results/bench_synthetic.json")
if c is None:
    raise SystemExit("CLAIMS.md is not committed; commit it before the first full benchmark run")
if r is None:
    raise SystemExit("results/bench_synthetic.json is not committed yet; commit CLAIMS.md first, then commit the results")
if c > r:
    raise SystemExit("CLAIMS.md was first committed after the results")
PY

check C14 "README discloses AI use and results carry provenance (git commit, cache off)" <<'PY'
need_bench()
if "AI use disclosure" not in open("README.md", encoding="utf-8").read():
    raise SystemExit("README.md has no 'AI use disclosure' section")
for f in ("bench_synthetic.json", "bench_wild.json"):
    m = json.load(open("results/" + f))["meta"]
    if len(m.get("git_commit", "")) != 40 or m.get("cache") is not False or not isinstance(m.get("http_requests"), int):
        raise SystemExit("%s meta lacks provenance: %s" % (f, m))
PY

if [ -n "$ONLY" ]; then
  echo "FILTERED RUN: only $ONLY were checked; this is not a full verification." >&2
  exit 3
fi
if [ "$FAILS" -ne 0 ]; then
  echo "$FAILS check(s) FAILED" >&2
  exit 1
fi
echo "ALL CHECKS PASSED"
exit 0
