# @exports: main(argv: list[str] | None = None) -> int
# @imports: config.py:load_config, load_thresholds
# @imports: pipeline.py:verify_reference, verify_many, verify_compare, report_dict
# @imports: schemas.py:Verdict
# @imports: extract.py:read_input, extract_references (M2, imported lazily by `file` and `extract`)
# @env: none
# @schema: CLI python -m citeproof.cli check "<ref>" [--mode weak|strong|evidence] [--json] [--cache]
# @schema: CLI python -m citeproof.cli file <path.txt|.md|.pdf> [--mode ...] [--compare] [--out results/report.json] [--workers 3] [--cache] [--json]
# @schema: CLI python -m citeproof.cli extract <path>   (no network; one reference per line, then "N references")
# @schema: exit codes 0 = all VERIFIED, 1 = at least one flagged, 2 = error; with --compare the exit code follows the "evidence" report
import argparse
import importlib
import json
import os
import sys

from citeproof.config import load_config, load_thresholds
from citeproof.pipeline import MODES, report_dict, verify_compare, verify_many, verify_reference
from citeproof.schemas import Verdict


def _extract_module():
    full = "citeproof.extract"
    try:
        return importlib.import_module(full)
    except ModuleNotFoundError as e:
        if e.name != full:
            raise
        raise RuntimeError(
            "citeproof/extract.py is not in the repo yet (it is added in milestone M2), "
            "so the `file` and `extract` commands are unavailable. Use `check` for now."
        ) from e


def _cite(c) -> str:
    names = ", ".join(c.authors[:3]) + (" et al." if len(c.authors) > 3 else "")
    return f"{names} ({c.year if c.year is not None else 'n.d.'}). {c.title}. {c.venue or ''}".strip()


def _render(v: Verdict, mode: str) -> str:
    lines = [
        f"{v.label}  (mode={mode}, confidence={v.confidence:.2f})",
        f"reasons: {', '.join(v.reasons)}",
    ]
    if v.best is not None:
        lines.append(f"best: {_cite(v.best)}")
        lines.append(f"doi: {v.best.doi or 'none'}")
        lines.append(f"url: {v.best.url or 'none'}")
        lines.append(f"source: {v.best.source}")
    if v.suggested:
        lines.append(f"suggested: {v.suggested}")
    lines.append(f"http_requests={v.http_requests} elapsed_ms={v.elapsed_ms}")
    return "\n".join(lines)


def _summary_line(report: dict) -> str:
    s = report["summary"]
    return (
        f"{report['mode']}: total={s['total']} verified={s['verified']} "
        f"mismatch={s['mismatch']} not_found={s['not_found']} http_requests={s['http_requests']}"
    )


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mode", choices=MODES, default="evidence", help="judging mode (default: evidence)")
    p.add_argument("--json", action="store_true", help="print the full JSON report")
    p.add_argument("--cache", action="store_true", help="dev-only HTTP memoization in .cache/http.sqlite")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m citeproof.cli", description="Audit references against Crossref/OpenAlex.")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="check one reference string")
    check.add_argument("ref", help="the reference text")
    _add_common(check)

    file_p = sub.add_parser("file", help="check every reference in a .txt, .md or .pdf file")
    file_p.add_argument("path")
    _add_common(file_p)
    file_p.add_argument("--compare", action="store_true", help="run weak, strong and evidence together (ignores --mode)")
    file_p.add_argument("--out", default=None, help="write the JSON report to this path, e.g. results/report.json")
    file_p.add_argument("--workers", type=int, default=3)

    extract = sub.add_parser("extract", help="list the references found in a file (no network)")
    extract.add_argument("path")
    return parser


def _warn_if_uncalibrated(calibrated: bool) -> None:
    if not calibrated:
        print("WARN: results/calibration.json not found; using default thresholds (uncalibrated).", file=sys.stderr)


def _cmd_check(args) -> int:
    cfg = load_config(use_cache=True if args.cache else None)
    th, calibrated = load_thresholds()
    _warn_if_uncalibrated(calibrated)
    verdict = verify_reference(1, args.ref, cfg, th, args.mode)
    if args.json:
        print(json.dumps(report_dict([verdict], args.mode), indent=2))
    else:
        print(_render(verdict, args.mode))
    return 0 if verdict.label == "VERIFIED" else 1


def _cmd_file(args) -> int:
    ext = _extract_module()
    refs = ext.extract_references(ext.read_input(args.path))
    if not refs:
        raise ValueError(f"No references found in {args.path}. Check that it has a References section or one reference per paragraph.")
    cfg = load_config(use_cache=True if args.cache else None)
    th, calibrated = load_thresholds()
    _warn_if_uncalibrated(calibrated)

    if args.compare:
        payload = verify_compare(refs, cfg, th, args.workers)
        gate = payload["evidence"]
        summaries = [_summary_line(payload[m]) for m in MODES]
    else:
        verdicts = verify_many(refs, cfg, th, args.mode, args.workers)
        payload = report_dict(verdicts, args.mode)
        gate = payload
        summaries = [_summary_line(payload)]

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for item in gate["items"]:
            raw = " ".join(item["raw"].split())
            print(f"[{item['ref_id']}] {item['label']:<9} {','.join(item['reasons'])}  | {raw[:80]}")
        for line in summaries:
            print(line)
        if args.out:
            print(f"wrote {args.out}")
    return 0 if gate["summary"]["verified"] == gate["summary"]["total"] else 1


def _cmd_extract(args) -> int:
    ext = _extract_module()
    refs = ext.extract_references(ext.read_input(args.path))
    for r in refs:
        print(" ".join(r.split()))
    print(f"{len(refs)} references")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "check":
            return _cmd_check(args)
        if args.command == "file":
            return _cmd_file(args)
        return _cmd_extract(args)
    except Exception as e:  # report loudly with exit code 2, never exit 1 (which means "flagged")
        print(f"ERROR ({type(e).__name__}): {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
