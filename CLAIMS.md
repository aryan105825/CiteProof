# CLAIMS

Commit this file before the first full benchmark run, and never edit the targets afterwards. If a target fails, the pitch changes to the measured number, not the threshold.

## Pre-registered targets (ASSUMED)

`scripts/verify.sh` reads the `TARGET` lines below.

```
TARGET synthetic_fpr_max 0.08
TARGET synthetic_f1_min 0.85
TARGET synthetic_recall_corrupted_margin 0.10
TARGET wild_fpr_max 0.20
TARGET wild_f1_min 0.75
```

- **Synthetic test:** FPR at most `synthetic_fpr_max`, F1(bad) at least `synthetic_f1_min`, and recall on corrupted references at least the **strong** baseline plus `synthetic_recall_corrupted_margin` (10 points).
- **Wild:** FPR at most `wild_fpr_max`, F1(bad) at least `wild_f1_min`, and recall strictly above the **strong** baseline.
- Calibration: all thresholds, including both baselines', are tuned on the train split only, maximizing F1(bad) subject to FPR <= 0.05.

## Claim ledger

Each claim is checked by `bash scripts/verify.sh`, which prints `PASS Cn` or `FAIL Cn`.

| ID | Claim |
|---|---|
| C1 | Crossref and OpenAlex answer all five smoke requests (`scripts/smoke.sh`). |
| C2 | "LeCun, Bengio, Hinton. Deep learning. Nature 521, 436-444 (2015)" is VERIFIED in strong mode with DOI 10.1038/nature14539, from a live call. |
| C3 | LeCun's title and authors with year 2011 and venue "Science" is MISMATCH in evidence mode. |
| C4 | A reference whose DOI belongs to another paper gets DOI_POINTS_ELSEWHERE. |
| C5 | A genuine CVPR-abbreviated reference stays VERIFIED. |
| C6 | `extract` finds the right reference count in a single-column PDF. |
| C7 | The API serves the page, reports health, and returns weak, strong and evidence reports with a corrected reference on a MISMATCH. |
| C8 | Synthetic test targets above are met, measured from `results/bench_synthetic.json`. |
| C9 | Wild targets above are met, measured from `results/bench_wild.json`. |
| C10 | The wild set has at least 100 rows, 3 chatbots and 12 topics, and second-labeller agreement covers at least 25 rows. |
| C11 | `results/error_analysis.json` lists up to 5 real failure cases. |
| C12 | The README results table contains the measured evidence-mode numbers from `results/`. |
| C13 | This file was committed no later than the first commit of `results/bench_synthetic.json`. |
| C14 | The README discloses AI use, and results carry provenance (git commit, cache off). |
