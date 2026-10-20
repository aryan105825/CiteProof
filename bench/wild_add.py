# @exports: main(argv: list[str] | None = None) -> int
# @imports: extract.py:read_input, extract_references
# @env: none
# @schema: CLI python -m bench.wild_add --chatbot NAME --model VERSION --topic TOPIC --file refs.txt [--source-type journal|book|preprint|other] [--date YYYY-MM-DD] [--out data/wild_labeled.csv]
# @schema: appends one row per extracted reference with id w001, w002, ... continuing from the file; label and evidence_url are left EMPTY on purpose, so bench.run_benchmark rejects the CSV until a human fills them in
# @schema: --source-type applies to every row of the run; edit individual rows by hand when a chatbot mixes books and articles
import argparse
import csv
import sys
from datetime import datetime, timezone

from citeproof.extract import extract_references, read_input

COLUMNS = ["id", "chatbot", "model_version", "date", "prompt_topic", "source_type", "raw", "label", "evidence_url", "notes"]
SOURCE_TYPES = ("journal", "book", "preprint", "other")


def _next_index(path: str) -> int:
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            raise ValueError(f"{path} header must be exactly {','.join(COLUMNS)}; got {reader.fieldnames}.")
        highest = 0
        for row in reader:
            rid = row["id"]
            if rid.startswith("w") and rid[1:].isdigit():
                highest = max(highest, int(rid[1:]))
    return highest + 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.wild_add", description="Append unlabelled chatbot references to the wild set.")
    p.add_argument("--chatbot", required=True)
    p.add_argument("--model", required=True, help="model version string as shown by the chatbot")
    p.add_argument("--topic", required=True, help="the prompt topic, e.g. 'transformer architectures'")
    p.add_argument("--file", required=True, help="text file with the chatbot's reference list, pasted verbatim")
    p.add_argument("--source-type", default="journal", choices=SOURCE_TYPES, dest="source_type")
    p.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    p.add_argument("--out", default="data/wild_labeled.csv")
    args = p.parse_args(argv)
    try:
        refs = extract_references(read_input(args.file))
        if not refs:
            raise ValueError(f"No references found in {args.file}. Paste the chatbot's reference list into it, one per line or paragraph.")
        start = _next_index(args.out)
        with open(args.out, "a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            for i, raw in enumerate(refs):
                writer.writerow([f"w{start + i:03d}", args.chatbot, args.model, args.date, args.topic, args.source_type, raw, "", "", ""])
        print(f"appended {len(refs)} rows to {args.out} (ids w{start:03d} to w{start + len(refs) - 1:03d}); now fill label and evidence_url by hand")
        return 0
    except Exception as e:
        print(f"ERROR ({type(e).__name__}): {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
