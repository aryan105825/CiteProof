# @exports: main(argv: list[str] | None = None) -> int
# @imports: baseline.py:baseline_verdict
# @imports: config.py:Thresholds, load_config
# @imports: pipeline.py:MODES, gather_candidates, judge
# @imports: scoring.py:decide, rank_and_evaluate
# @env: CITEPROOF_MAILTO
# @env: CITEPROOF_RATE
# @env: CITEPROOF_TIMEOUT
# @env: CITEPROOF_CACHE
# @env: CITEPROOF_OPENALEX
# @schema: CLI python -m bench.run_benchmark --records data/bench_records.json --wild data/wild_labeled.csv --out results [--workers 3] [--wild-second data/wild_labeled_second.csv]
# @schema: --wild-second (beyond CONTRACTS) is a CSV with header id,label holding the second labeller's labels for >=25 wild rows
# @schema: writes results/calibration.json, bench_synthetic.json, bench_wild.json, wild_stats.json, error_analysis.json; every gather is live with the cache forced off
# @schema: calibration: grid over title_ok 80-95 (5), title_partial 50-80 (5), author_frac_ok {.34,.5,.67}, residual_max .30-.70 (.05), maximize F1(bad) s.t. FPR<=0.05 on TRAIN; baselines' title thresholds 50..100 under the same constraint; if no setting meets the FPR cap the lowest-FPR setting is written and train_metrics.constraint_met says false
# @schema: bench_*.json mode blocks add recall_corrupted and recall_fabricated (null when the set has none) beyond CONTRACTS; recall_by_mutation is keyed by mutation (synthetic) or by label (wild)
# @schema: error_analysis.json {"n_errors_total": int, "cases": [up to 5 evidence-mode errors, wild first then synthetic test]}
import argparse
import csv
import json
import math
import os
import platform
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from citeproof.baseline import baseline_verdict
from citeproof.config import Thresholds, load_config
from citeproof.pipeline import MODES, gather_candidates, judge
from citeproof.scoring import decide, rank_and_evaluate

FPR_CAP = 0.05
TITLE_OK_GRID = [80, 85, 90, 95]
TITLE_PARTIAL_GRID = [50, 55, 60, 65, 70, 75, 80]
AUTHOR_GRID = [0.34, 0.5, 0.67]
RESIDUAL_GRID = [round(0.30 + 0.05 * i, 2) for i in range(9)]
BASELINE_GRID = list(range(50, 101))
CORE_FIELDS = ("title_ok", "title_partial", "author_frac_ok", "residual_max", "year_tol", "year_tol_preprint")
WILD_COLUMNS = ["id", "chatbot", "model_version", "date", "prompt_topic", "source_type", "raw", "label", "evidence_url", "notes"]
LABELS = ("valid", "corrupted", "fabricated")
SOURCE_TYPES = ("journal", "book", "preprint", "other")


def _git_commit() -> str:
    p = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(
            "git rev-parse HEAD failed, so results cannot record provenance. "
            "Run inside a git repository with at least one commit (git init && git add -A && git commit)."
        )
    return p.stdout.strip()


def _load_records(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items") if isinstance(data, dict) else None
    if not items:
        raise ValueError(f"{path} has no items. Build it with `python -m bench.build_dataset`.")
    if sum(1 for it in items if it["split"] == "train") < 40 or sum(1 for it in items if it["split"] == "test") < 40:
        raise ValueError(f"{path} needs at least 40 train and 40 test items. Rebuild with a larger --n.")
    return items


def _load_wild(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
        rows = list(reader)
    if header != WILD_COLUMNS:
        raise ValueError(f"{path} header must be exactly {','.join(WILD_COLUMNS)}; got {header}.")
    problems: list[str] = []
    seen: set[str] = set()
    for i, r in enumerate(rows, start=2):
        where = f"line {i}"
        if not r["id"].strip() or r["id"] in seen:
            problems.append(f"{where}: id missing or duplicated")
        seen.add(r["id"])
        if r["label"] not in LABELS:
            problems.append(f"{where}: label {r['label']!r} not in {LABELS}")
        if r["source_type"] not in SOURCE_TYPES:
            problems.append(f"{where}: source_type {r['source_type']!r} not in {SOURCE_TYPES}")
        for col in ("chatbot", "prompt_topic", "raw"):
            if not r[col].strip():
                problems.append(f"{where}: {col} is empty")
        if r["label"] in ("valid", "corrupted") and not r["evidence_url"].strip():
            problems.append(f"{where}: evidence_url is required for {r['label']} rows")
    if len(rows) < 100:
        problems.append(f"only {len(rows)} rows; at least 100 are required")
    if len({r["chatbot"] for r in rows}) < 3:
        problems.append("fewer than 3 distinct chatbots")
    if len({r["prompt_topic"] for r in rows}) < 12:
        problems.append("fewer than 12 distinct prompt_topic values")
    if problems:
        shown = "\n- ".join(problems[:20])
        raise ValueError(f"{path} is not a valid wild set ({len(problems)} problems):\n- {shown}\nFix the CSV; it is human-made and never auto-filled.")
    return [
        {"id": r["id"], "split": "wild", "label": r["label"], "mutation": None, "raw": r["raw"],
         "chatbot": r["chatbot"], "source_type": r["source_type"]}
        for r in rows
    ]


def _load_second(path: str, wild_ids: set[str]) -> dict[str, str]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ["id", "label"]:
            raise ValueError(f"{path} header must be exactly id,label; got {reader.fieldnames}.")
        rows = list(reader)
    out: dict[str, str] = {}
    for r in rows:
        if r["id"] not in wild_ids:
            raise ValueError(f"{path}: id {r['id']!r} is not in the wild set.")
        if r["label"] not in LABELS:
            raise ValueError(f"{path}: label {r['label']!r} for {r['id']!r} not in {LABELS}.")
        if r["id"] in out:
            raise ValueError(f"{path}: duplicate id {r['id']!r}.")
        out[r["id"]] = r["label"]
    if len(out) < 25:
        raise ValueError(f"{path} has {len(out)} rows; the second labeller must cover at least 25.")
    return out


def _cohen_kappa(a: list[str], b: list[str]) -> float:
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in LABELS)
    if pe >= 1.0:
        return 1.0  # both labellers used a single identical category; agreement is perfect, kappa is otherwise undefined
    return (po - pe) / (1.0 - pe)


def _gather_all(raws: list[str], cfg, workers: int) -> list:
    out = []
    chunk = 50
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(raws), chunk):
            out.extend(pool.map(lambda r: gather_candidates(r, cfg), raws[start:start + chunk]))
            print(f"gathered {len(out)}/{len(raws)}", file=sys.stderr)
    return out


def _metrics(flagged: list[bool], bad: list[bool]) -> dict:
    tp = sum(1 for f, b in zip(flagged, bad) if f and b)
    fp = sum(1 for f, b in zip(flagged, bad) if f and not b)
    fn = sum(1 for f, b in zip(flagged, bad) if not f and b)
    n_valid = sum(1 for b in bad if not b)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "fpr": fp / n_valid if n_valid else 0.0,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
    }


def _select(options: list[tuple[dict, dict]]) -> tuple[tuple[dict, dict], bool]:
    feasible = [o for o in options if o[1]["fpr"] <= FPR_CAP]
    if feasible:
        best = feasible[0]
        for o in feasible[1:]:
            if o[1]["f1"] > best[1]["f1"] + 1e-12 or (abs(o[1]["f1"] - best[1]["f1"]) <= 1e-12 and o[1]["fpr"] < best[1]["fpr"]):
                best = o
        return best, True
    return min(options, key=lambda o: (o[1]["fpr"], -o[1]["f1"])), False


def _calibrate(train: list[dict], gathers: list) -> dict:
    bad = [it["label"] != "valid" for it in train]
    evaluated = [rank_and_evaluate(it["raw"], g, Thresholds())[1] for it, g in zip(train, gathers)]

    options = []
    for title_ok in TITLE_OK_GRID:
        for title_partial in TITLE_PARTIAL_GRID:
            for author_frac in AUTHOR_GRID:
                for residual in RESIDUAL_GRID:
                    th = Thresholds(title_ok=float(title_ok), title_partial=float(title_partial),
                                    author_frac_ok=author_frac, residual_max=residual)
                    flagged = [True if ev is None else decide(ev, th)[0] != "VERIFIED" for ev in evaluated]
                    params = {k: getattr(th, k) for k in CORE_FIELDS}
                    options.append((params, _metrics(flagged, bad)))
    (core, core_metrics), core_ok = _select(options)

    base = {}
    for key, variant in (("weak", "title"), ("strong", "title_author")):
        opts = []
        for thr in BASELINE_GRID:
            flagged = [baseline_verdict(i, it["raw"], g, float(thr), variant).label != "VERIFIED"
                       for i, (it, g) in enumerate(zip(train, gathers), start=1)]
            opts.append(({"title_threshold": float(thr)}, _metrics(flagged, bad)))
        (params, metrics), ok = _select(opts)
        base[key] = (params["title_threshold"], metrics, ok)

    return {
        "core": core, "core_metrics": core_metrics, "core_ok": core_ok,
        "weak": base["weak"], "strong": base["strong"],
    }


def _percentile(values: list[int], pct: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100.0 * len(ordered)) - 1)]


def _evaluate(items: list[dict], gathers: list, th: Thresholds, commit: str) -> tuple[dict, dict]:
    bad = [it["label"] != "valid" for it in items]
    verdicts = {m: [judge(i, it["raw"], g, th, m) for i, (it, g) in enumerate(zip(items, gathers), start=1)] for m in MODES}
    names = {"weak": "baseline_weak", "strong": "baseline_strong", "evidence": "evidence"}
    result: dict = {
        "meta": {
            "git_commit": commit,
            "python": platform.python_version(),
            "date_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n": len(items),
            "cache": False,
            "http_requests": sum(g.http_requests for g in gathers),
        }
    }
    for mode in MODES:
        flagged = [v.label != "VERIFIED" for v in verdicts[mode]]
        block = _metrics(flagged, bad)
        groups: dict[str, list[bool]] = {}
        for it, f in zip(items, flagged):
            if it["label"] != "valid":
                groups.setdefault(it["mutation"] or it["label"], []).append(f)
        block["recall_by_mutation"] = {k: sum(v) / len(v) for k, v in sorted(groups.items())}
        for lab in ("corrupted", "fabricated"):
            fl = [f for it, f in zip(items, flagged) if it["label"] == lab]
            block[f"recall_{lab}"] = (sum(fl) / len(fl)) if fl else None
        if mode == "evidence":
            block["confusion3"] = {
                lab: {c: sum(1 for it, v in zip(items, verdicts[mode]) if it["label"] == lab and v.label == c)
                      for c in ("VERIFIED", "MISMATCH", "NOT_FOUND")}
                for lab in LABELS
            }
        result[names[mode]] = block
    lat = [g.elapsed_ms for g in gathers]
    result["latency_ms"] = {"p50": _percentile(lat, 50), "p95": _percentile(lat, 95)}
    return result, verdicts


def _dump(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _error_cases(wild, wild_verdicts, test, test_verdicts) -> tuple[int, list[dict]]:
    errors = []
    for source, items, verdicts in (("wild", wild, wild_verdicts), ("synthetic", test, test_verdicts)):
        for it, v in zip(items, verdicts):
            is_bad = it["label"] != "valid"
            if (is_bad and v.label == "VERIFIED") or (not is_bad and v.label != "VERIFIED"):
                errors.append({
                    "source": source, "id": it["id"], "truth_label": it["label"], "mutation": it["mutation"],
                    "raw": it["raw"], "verdict": v.label, "reasons": v.reasons,
                    "best_title": v.best.title if v.best else None, "best_doi": v.best.doi if v.best else None,
                })
    return len(errors), errors[:5]


def _run(args) -> None:
    cfg = load_config(use_cache=False)
    commit = _git_commit()
    records = _load_records(args.records)
    wild = _load_wild(args.wild)
    second = _load_second(args.wild_second, {w["id"] for w in wild})
    train = [it for it in records if it["split"] == "train"]
    test = [it for it in records if it["split"] == "test"]

    everything = train + test + wild
    gathers = _gather_all([it["raw"] for it in everything], cfg, args.workers)
    g_train = gathers[:len(train)]
    g_test = gathers[len(train):len(train) + len(test)]
    g_wild = gathers[len(train) + len(test):]

    cal = _calibrate(train, g_train)
    th = Thresholds(**cal["core"], baseline_title_weak=cal["weak"][0], baseline_title_strong=cal["strong"][0])
    for name, ok in (("evidence", cal["core_ok"]), ("weak baseline", cal["weak"][2]), ("strong baseline", cal["strong"][2])):
        if not ok:
            print(f"WARN: no {name} setting reaches FPR <= {FPR_CAP} on train; wrote the lowest-FPR setting.", file=sys.stderr)

    os.makedirs(args.out, exist_ok=True)
    _dump(os.path.join(args.out, "calibration.json"), {
        "thresholds": cal["core"],
        "baseline_thresholds": {"weak": cal["weak"][0], "strong": cal["strong"][0]},
        "n_train": len(train),
        "train_ids": [it["id"] for it in train],
        "train_metrics": {
            "evidence": cal["core_metrics"], "baseline_weak": cal["weak"][1], "baseline_strong": cal["strong"][1],
            "fpr_cap": FPR_CAP,
            "constraint_met": {"evidence": cal["core_ok"], "weak": cal["weak"][2], "strong": cal["strong"][2]},
        },
    })

    synthetic, test_verdicts = _evaluate(test, g_test, th, commit)
    wild_result, wild_verdicts = _evaluate(wild, g_wild, th, commit)
    _dump(os.path.join(args.out, "bench_synthetic.json"), synthetic)
    _dump(os.path.join(args.out, "bench_wild.json"), wild_result)

    ev_flags = [v.label != "VERIFIED" for v in wild_verdicts["evidence"]]
    by_chatbot = Counter(w["chatbot"] for w in wild)
    bad_by_chatbot = {c: sum(1 for w in wild if w["chatbot"] == c and w["label"] != "valid") / n for c, n in sorted(by_chatbot.items())}
    fpr_by_type = {}
    for t in SOURCE_TYPES:
        valid_flags = [f for w, f in zip(wild, ev_flags) if w["source_type"] == t and w["label"] == "valid"]
        fpr_by_type[t] = (sum(valid_flags) / len(valid_flags)) if valid_flags else None
    wild_by_id = {w["id"]: w["label"] for w in wild}
    ids = sorted(second)
    _dump(os.path.join(args.out, "wild_stats.json"), {
        "n": len(wild),
        "by_chatbot": dict(sorted(by_chatbot.items())),
        "label_counts": dict(Counter(w["label"] for w in wild)),
        "bad_fraction": sum(1 for w in wild if w["label"] != "valid") / len(wild),
        "bad_fraction_by_chatbot": bad_by_chatbot,
        "fpr_wild_valid_by_type": fpr_by_type,
        "labeller_agreement": {"n": len(ids), "cohen_kappa": _cohen_kappa([wild_by_id[i] for i in ids], [second[i] for i in ids])},
    })

    n_err, cases = _error_cases(wild, wild_verdicts["evidence"], test, test_verdicts["evidence"])
    _dump(os.path.join(args.out, "error_analysis.json"), {"n_errors_total": n_err, "cases": cases})

    for name, res in (("synthetic", synthetic), ("wild", wild_result)):
        e, s = res["evidence"], res["baseline_strong"]
        print(f"{name}: evidence F1={e['f1']:.3f} recall={e['recall']:.3f} FPR={e['fpr']:.3f} | strong recall={s['recall']:.3f} FPR={s['fpr']:.3f}")
    print(f"wrote results to {args.out}/")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.run_benchmark", description="Calibrate on train, evaluate on test and wild.")
    p.add_argument("--records", default="data/bench_records.json")
    p.add_argument("--wild", default="data/wild_labeled.csv")
    p.add_argument("--wild-second", default="data/wild_labeled_second.csv", dest="wild_second")
    p.add_argument("--out", default="results")
    p.add_argument("--workers", type=int, default=3)
    args = p.parse_args(argv)
    try:
        _run(args)
        return 0
    except Exception as e:
        print(f"ERROR ({type(e).__name__}): {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
