# @exports: main(argv: list[str] | None = None) -> int
# @imports: none
# @env: none
# @schema: CLI python -m bench.wild_merge --part PREFIX=path.csv [--part ...] [--set ID.FIELD=VALUE ...] [--out data/wild_labeled.csv] [--force]
# @schema: each part keeps its rows but its ids are rewritten to PREFIX-NNN (the numeric tail of the old id), so runs that reused the same id prefix cannot collide
# @schema: --set edits label, source_type or evidence_url of a merged id and appends "[author edit: FIELD old -> new]" to its notes; chatbot and model_version are whitespace-normalized, raw is only trimmed
# @schema: always writes --out, then exits 1 (NOT READY) if any label is empty, fewer than 3 distinct chatbot values, or fewer than 12 topics or 100 rows; it never relabels chatbots to satisfy the count
import argparse
import csv
import os
import re
import sys

COLUMNS = ["id", "chatbot", "model_version", "date", "prompt_topic", "source_type", "raw", "label", "evidence_url", "notes"]
LABELS = ("valid", "corrupted", "fabricated")
SOURCE_TYPES = ("journal", "book", "preprint", "other")
EDITABLE = ("label", "source_type", "evidence_url")


def _read_part(prefix: str, path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            raise ValueError(f"{path} header must be exactly {','.join(COLUMNS)}; got {reader.fieldnames}.")
        rows = list(reader)
    out = []
    for r in rows:
        m = re.search(r"(\d+)$", r["id"].strip())
        if m is None:
            raise ValueError(f"{path}: id {r['id']!r} has no numeric tail to renumber.")
        row = {k: (v or "").strip() for k, v in r.items()}
        row["id"] = f"{prefix}-{int(m.group(1)):03d}"
        row["chatbot"] = " ".join(row["chatbot"].split())
        row["model_version"] = " ".join(row["model_version"].split())
        out.append(row)
    return out


def _parse_set(spec: str) -> tuple[str, str, str]:
    m = re.fullmatch(r"([\w-]+)\.(\w+)=(.*)", spec)
    if m is None or m.group(2) not in EDITABLE:
        raise ValueError(f"--set {spec!r} must look like ID.FIELD=VALUE with FIELD in {EDITABLE}.")
    return m.group(1), m.group(2), m.group(3)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.wild_merge", description="Merge chatbot CSV parts into the wild set.")
    p.add_argument("--part", action="append", required=True, help="PREFIX=path.csv, e.g. gpt=data/wild_part_chatgpt.csv")
    p.add_argument("--set", action="append", default=[], dest="edits", help="ID.FIELD=VALUE author edit, e.g. gmf-031.label=valid")
    p.add_argument("--out", default="data/wild_labeled.csv")
    p.add_argument("--force", action="store_true", help="overwrite --out even if it already has rows")
    args = p.parse_args(argv)
    try:
        if os.path.exists(args.out) and not args.force:
            with open(args.out, "r", encoding="utf-8", newline="") as f:
                existing = sum(1 for _ in csv.DictReader(f))
            if existing:
                raise ValueError(f"{args.out} already has {existing} rows. Re-run with --force to overwrite it.")

        rows: list[dict] = []
        seen_prefixes: set[str] = set()
        for spec in args.part:
            prefix, sep, path = spec.partition("=")
            if not sep or not re.fullmatch(r"[a-z]{2,6}", prefix):
                raise ValueError(f"--part {spec!r} must look like PREFIX=path.csv with a 2-6 letter lowercase PREFIX.")
            if prefix in seen_prefixes:
                raise ValueError(f"Prefix {prefix!r} is used twice; every part needs its own prefix.")
            seen_prefixes.add(prefix)
            rows.extend(_read_part(prefix, path))

        ids = [r["id"] for r in rows]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"Duplicate ids after renumbering: {dupes[:5]}. A part has repeated numeric ids.")

        by_id = {r["id"]: r for r in rows}
        for spec in args.edits:
            rid, field, value = _parse_set(spec)
            if rid not in by_id:
                raise ValueError(f"--set refers to unknown id {rid!r}.")
            row = by_id[rid]
            note = f"[author edit: {field} {row[field] or '(empty)'} -> {value}]"
            row[field] = value
            row["notes"] = (row["notes"] + " " + note).strip()

        problems = []
        empty = [r["id"] for r in rows if r["label"] == ""]
        bad_label = [r["id"] for r in rows if r["label"] not in LABELS + ("",)]
        bad_type = [r["id"] for r in rows if r["source_type"] not in SOURCE_TYPES]
        if bad_label:
            problems.append(f"invalid label on {bad_label}")
        if bad_type:
            problems.append(f"invalid source_type on {bad_type}")
        if empty:
            problems.append(f"{len(empty)} empty labels (fill by hand): {', '.join(empty)}")
        chatbots = sorted({r["chatbot"] for r in rows})
        topics = {r["prompt_topic"] for r in rows}
        if len(chatbots) < 3:
            problems.append(f"only {len(chatbots)} distinct chatbot values {chatbots}; the pack needs at least 3 chatbots")
        if len(topics) < 12:
            problems.append(f"only {len(topics)} topics; at least 12 are needed")
        if len(rows) < 100:
            problems.append(f"only {len(rows)} rows; at least 100 are needed")

        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

        counts = {lab: sum(1 for r in rows if r["label"] == lab) for lab in LABELS}
        print(f"wrote {args.out}: {len(rows)} rows, {len(chatbots)} chatbots {chatbots}, {len(topics)} topics")
        print(f"labels: {counts}, empty: {len(empty)}")
        if problems:
            print("NOT READY:", file=sys.stderr)
            for pr in problems:
                print(f"- {pr}", file=sys.stderr)
            return 1
        print("READY: the wild set passes the structural checks")
        return 0
    except Exception as e:
        print(f"ERROR ({type(e).__name__}): {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
