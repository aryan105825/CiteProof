# @exports: main(argv: list[str] | None = None) -> int
# @exports: DEFAULT_FILTER: str
# @imports: crossref.py:sample_records
# @imports: config.py:load_config
# @imports: format.py:format_ref
# @imports: textnorm.py:fold
# @env: CITEPROOF_MAILTO
# @env: CITEPROOF_RATE
# @env: CITEPROOF_TIMEOUT
# @env: CITEPROOF_CACHE
# @env: CITEPROOF_OPENALEX
# @schema: CLI python -m bench.build_dataset [--n 400] [--seed 7] [--out data/bench_records.json]
# @schema: output {"meta":{"seed","n","filter","built_at","source"},"items":[{"id","split","style","label","mutation","raw","truth"}]}
# @schema: per split: 40% valid, 40% corrupted (swap_year/swap_authors/swap_title/swap_venue equally), 20% fabricated (splice_fabricate: authors, title and venue/year taken from three different records); donors come from the same split; train/test is a 50/50 split by source record
# @schema: DOIs are stripped from every synthetic reference (raw never contains a DOI) so no mode gets a DOI shortcut; "truth" keeps the source record including its DOI
import argparse
import json
import os
import random
import sys
from dataclasses import asdict, replace
from datetime import datetime, timezone

from citeproof import crossref
from citeproof.config import load_config
from citeproof.format import format_ref
from citeproof.schemas import Candidate
from citeproof.textnorm import fold

DEFAULT_FILTER = "type:journal-article,from-pub-date:2000-01-01,until-pub-date:2025-12-31"
STYLES = ("apa", "ieee", "vancouver", "messy")
OPS = ("swap_year", "swap_authors", "swap_title", "swap_venue")


def _donor(rng: random.Random, pool: list[Candidate], src: Candidate, ok, avoid: tuple = ()) -> Candidate:
    for _ in range(500):
        d = rng.choice(pool)
        if d is not src and all(d is not a for a in avoid) and ok(d):
            return d
    raise RuntimeError("Could not find a suitable donor record after 500 tries. Re-run with a larger --n.")


def _mutate(rng: random.Random, src: Candidate, op: str, pool: list[Candidate]) -> Candidate:
    if op == "swap_year":
        delta = rng.choice([-6, -5, -4, -3, 3, 4, 5, 6])
        year = src.year + delta
        if year > 2026:
            year = src.year - abs(delta)
        return replace(src, year=year, doi=None)
    if op == "swap_authors":
        d = _donor(rng, pool, src, lambda d: bool(d.authors) and fold(d.authors[0]) != fold(src.authors[0]))
        return replace(src, authors=list(d.authors), doi=None)
    if op == "swap_title":
        d = _donor(rng, pool, src, lambda d: fold(d.title) != fold(src.title))
        return replace(src, title=d.title, doi=None)
    if op == "swap_venue":
        d = _donor(rng, pool, src, lambda d: bool(d.venue) and fold(d.venue) != fold(src.venue or ""))
        return replace(src, venue=d.venue, doi=None)
    raise ValueError(f"Unknown mutation {op!r}; expected one of {OPS}.")


def _fabricate(rng: random.Random, src: Candidate, pool: list[Candidate]) -> Candidate:
    a = _donor(rng, pool, src, lambda d: bool(d.authors) and fold(d.authors[0]) != fold(src.authors[0]))
    b = _donor(rng, pool, src, lambda d: fold(d.title) != fold(src.title), avoid=(a,))
    return replace(src, authors=list(a.authors), title=b.title, doi=None, index_score=None)


def build(n: int, seed: int, out: str) -> None:
    if n < 40:
        raise ValueError(f"--n must be at least 40 so every split has enough donors, got {n}.")
    cfg = load_config(use_cache=False)
    recs = crossref.sample_records(n, cfg, DEFAULT_FILTER)
    rng = random.Random(seed)
    order = list(range(len(recs)))
    rng.shuffle(order)
    half = len(order) // 2
    splits = {"train": [recs[i] for i in order[:half]], "test": [recs[i] for i in order[half:]]}

    items = []
    for split in ("train", "test"):
        pool = splits[split]
        m = len(pool)
        n_valid = round(0.4 * m)
        n_fab = round(0.2 * m)
        labels = ["valid"] * n_valid + ["corrupted"] * (m - n_valid - n_fab) + ["fabricated"] * n_fab
        rng.shuffle(labels)
        cor_i = 0
        for k, (src, label) in enumerate(zip(pool, labels)):
            style = rng.choice(STYLES)
            mutation = None
            if label == "valid":
                cand = replace(src, doi=None)
            elif label == "corrupted":
                mutation = OPS[cor_i % len(OPS)]
                cor_i += 1
                cand = _mutate(rng, src, mutation, pool)
            else:
                mutation = "splice_fabricate"
                cand = _fabricate(rng, src, pool)
            items.append({
                "id": f"{split}-{k:04d}",
                "split": split,
                "style": style,
                "label": label,
                "mutation": mutation,
                "raw": format_ref(cand, style, rng),
                "truth": asdict(src),
            })

    payload = {
        "meta": {
            "seed": seed,
            "n": len(items),
            "filter": DEFAULT_FILTER,
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "Crossref /works?sample",
        },
        "items": items,
    }
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"wrote {out}: {len(items)} items ({len(splits['train'])} train / {len(splits['test'])} test), seed={seed}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.build_dataset", description="Build the frozen synthetic benchmark.")
    p.add_argument("--n", type=int, default=400)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", default="data/bench_records.json")
    args = p.parse_args(argv)
    try:
        build(args.n, args.seed, args.out)
        return 0
    except Exception as e:
        print(f"ERROR ({type(e).__name__}): {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
