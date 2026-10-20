# CiteProof

CiteProof audits references (pasted text or a PDF) against live Crossref and OpenAlex records. It catches **chimera citations**, whose fields do not all belong to one real paper, and offers a corrected reference built from the closest real record.

It is deterministic and explainable: no LLM, no API key. It detects inconsistent citations, not AI-written text.

## The idea

A citation is genuine only if **one index record explains all of its fields**. Hallucinated references fail on cross-field consistency, not on existence: a real author list with an invented title, a real title with the wrong year, a DOI that belongs to a different paper. Existence-only checkers miss all three.

Each reference gets one of three labels, with reason codes and a link to the closest real record:

| Label | Meaning |
|---|---|
| `VERIFIED` | One record explains the title, first author, year, any cited DOI, and nearly all of the remaining text. |
| `MISMATCH` | A real record exists, but some fields do not belong to it. A corrected reference is offered. |
| `NOT_FOUND` | Nothing in the indexes is close enough on title or authors. |

Flow: extract, gather candidates once, compute field evidence, decide, report.

## Setup

Python **3.12** is required. Newer interpreters (3.14) have no prebuilt `rapidfuzz` wheel for the pinned version and the install fails.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set CITEPROOF_MAILTO to your own email
set -a; source .env; set +a
bash scripts/smoke.sh           # checks that Crossref and OpenAlex answer
```

All environment variables are listed in `.env.example` and read in one place, `citeproof/config.py`.

## Use

**Web app**

```bash
uvicorn citeproof.api:app --port 8000     # open http://localhost:8000
```

Paste references or drop a PDF. A control above the results switches between the two baselines and CiteProof on the same data, so you can see exactly which references each checker lets through.

**CLI** (exit code 0 = all verified, 1 = something flagged, 2 = error)

```bash
python -m citeproof.cli check "LeCun, Bengio, Hinton. Deep learning. Science 521, 436-444 (2011)"
python -m citeproof.cli file refs.pdf --compare --out results/report.json
python -m citeproof.cli extract refs.pdf          # no network
```

**Container**

```bash
docker build -t citeproof . && docker run -p 8000:8000 -e CITEPROOF_MAILTO=you@example.com citeproof
```

`render.yaml` deploys the same image to Render; set `CITEPROOF_MAILTO` in the dashboard.

## Evidence and verification

One command checks the data, runs the benchmark and verifies every claim:

```bash
bash scripts/run_all.sh
```

It stops with a clear message if the wild set is not ready (empty labels, too few rows, no second labeller), then runs `scripts/verify.sh`, which can also be run on its own.

It prints `PASS Cn` or `FAIL Cn` for each claim in [`CLAIMS.md`](CLAIMS.md), runs the benchmark live with the cache off, and writes the measured numbers to `results/*.json`. A failing target changes the pitch, never the threshold.

- **Baselines.** A weak one (does the title exist?) and a strong one (does it exist and is the first author named?). Headline gains are reported against the strong one.
- **Calibration.** Every threshold, including the baselines', is tuned on a train split only, maximizing F1 on bad references subject to a false-alarm rate of at most 5%.
- **Synthetic benchmark.** 400 Crossref records, split 50/50 by source record: 40% valid, 40% corrupted (swapped year, authors, title or venue), 20% fabricated by splicing three real records.
- **Wild set.** `data/wild_labeled.csv`: references produced by real chatbots, each looked up and labelled by hand, with a second labeller on at least 25 rows. Never used for tuning.

### Building the wild set

```bash
# paste a chatbot's reference list into refs.txt exactly as it answered, then:
python -m bench.wild_add --chatbot "ChatGPT" --model "<version shown>" --topic "<prompt topic>" --file refs.txt
```

Rows are appended with `label` and `evidence_url` empty on purpose. Look each reference up yourself (publisher page, Crossref, Google Scholar), then set `label` to `valid`, `corrupted` or `fabricated` and record the page you used. Fix `source_type` per row where a chatbot mixed books and articles. The second labeller fills `data/wild_labeled_second.csv` (`id,label`) without seeing your labels.

### Results

The table below is pasted from `results/summary.md` after a full `verify.sh` run, so every number comes from `results/*.json`.

## Limits

- English only; single-column PDFs only.
- Coverage is bounded by Crossref and OpenAlex. Books and grey literature are often unindexed, so `NOT_FOUND` means "check by hand", not "fake".
- Citation records store family names only, so the corrected reference has no author initials.
- The wild set is small, so rates from it carry wide uncertainty.
- The wild set covers three chatbot models: Gemini Flash, Gemini Pro and ChatGPT. Two of the three are from the same vendor.

## Layout

```
citeproof/   schemas, config, textnorm, httpclient, crossref, openalex, format, baseline,
             scoring, pipeline, extract, cli, api
web/         the single-page app
bench/       build_dataset, run_benchmark, wild_add
scripts/     smoke.sh, verify.sh
data/        bench_records.json (generated, frozen), wild_labeled*.csv (human-made)
results/     calibration, benchmark, stats and error-analysis JSON (generated)
```

## AI use disclosure

AI assistance (Claude) was used to write much of this code, including the app, the benchmark and the verification script. All thresholds and every reported number come from running the code. The wild-set labels were produced with AI-assisted lookups (each chatbot's own browsing, plus Claude web search and a Gemini agent for the remaining rows) and checked by the author against the cited records. A second independent AI agent (a separate session on a different model) labelled 30 rows blind, so the reported agreement is between two AI labellers, not against a human.
