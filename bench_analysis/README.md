# Bench Analysis MVP

This is a minimal pipeline for the first demo of a general Bench analysis system.

Current implemented features:

- capability tagging
- paper-note-style benchmark analysis sections
- source discovery
- raw source fetching
- heuristic field extraction
- noisy model-score row extraction
- source reconciliation and conflict notes

It takes a Bench name, resolves it against a small seed catalog, maps it into a
shared `BenchProfile` schema, and writes JSON plus static HTML.

Single-Bench HTML reports now follow the Benchmark Paper Analysis template:

1. Core Question & Motivation
2. Evaluated Capability
3. Benchmark / Task Design
4. Rubric, Gold & Scoring
5. Model Results
6. Main Findings & Conclusions
7. Failure Modes
8. Reliability / Reproducibility Notes
9. Sources & Evidence

## Run

From `content-pipeline`:

```bash
python3 -m bench_analysis batch
python3 -m bench_analysis analyze "SpreadsheetBench v2"
python3 -m bench_analysis analyze "GDPval" --with-web --discovery-limit 6 --fetch-limit 3
python3 -m bench_analysis discover "FAB" --limit 6
python3 -m bench_analysis list
```

Milestone 1 adds a batch-first job runner:

```bash
python3 -m bench_analysis job-run "GDPval" "FAB" "SpreadsheetBench v2" --no-web
python3 -m bench_analysis job-run "GDPval" "FAB" --discovery-limit 5 --fetch-limit 3
python3 -m bench_analysis job-list
python3 -m bench_analysis job-show JOB_ID
python3 -m bench_analysis brief-prototype --lang zh-CN
python3 -m bench_analysis web
```

`job-run` writes each batch into an isolated directory:

```text
bench_analysis_outputs/
  bench_jobs.sqlite
  jobs/
    JOB_ID/
      job.json
      index.html
      gdpval/
        profile.json
        report.html
        raw/
```

`job.json` is the manifest for the batch. It records job status, options, bench
run status, step status, output paths, warning counts, raw fetch failures,
conflict counts, and missing core fields.

Warning/error semantics:

- `error`: a bench run or pipeline step failed.
- `warning`: the run completed but needs review, such as ambiguous identity,
  unresolved identity, missing core fields, raw fetch failures, or field
  conflicts.

The manifest includes both absolute output paths and relative artifact paths, so
future Web UI code can read one `job.json` without querying SQLite for basic
report navigation.

`brief-prototype` generates Chinese sky-blue research brief prototypes:

```text
bench_analysis_outputs/research_briefs/
  index.html
  apex.html
  onemillion-bench.html
  spreadsheetbench-v2.html
```

These pages are explicitly marked as `原型样例` / `视觉与结构原型`.
The Chinese body text comes from `brief_localization.py` display-layer
overrides, while source/artifact links remain in their original raw form for
traceability.

Generate the prototype:

```bash
python3 -m bench_analysis brief-prototype --lang zh-CN
open bench_analysis_outputs/research_briefs/index.html
```

## Web UI

Start the local workbench:

```bash
python3 -m bench_analysis web --host 127.0.0.1 --port 8765
```

Then open:

```text
http://127.0.0.1:8765
```

The UI supports:

- creating a batch job from multiple Bench names
- toggling `with_web`
- setting discovery/fetch limits
- viewing recent jobs
- watching job progress
- opening exported batch HTML, `job.json`, per-Bench HTML, and per-Bench JSON

`--with-web` runs the full MVP pipeline:

```text
source_discovery.py -> fetch.py -> extract.py / results.py -> reconcile.py -> JSON + HTML
```

By default, discovery uses seeded sources plus arXiv, GitHub, and Hugging Face
APIs. Generic search-engine HTML scraping is available but off by default because
it is often blocked by anti-automation pages:

```bash
python3 -m bench_analysis discover "FAB" --include-general-search
```

Outputs are written to:

```text
bench_analysis_outputs/
  index.html
  gdpval/profile.json
  gdpval/report.html
  ...
```

## Train/Test Split

Training benches:

- GDPval
- SpreadsheetBench v2
- FAB

Testing benches:

- APEX
- FinSearchComp
- OneMillion-Bench
- IBFE

`IBFE` is intentionally kept as an ambiguous low-confidence case, so the demo can
show how the pipeline handles unresolved benchmark names.

## Next Steps

- Replace heuristic extraction with LLM-backed extraction plus source citations.
- Add table-specific parsers for leaderboard and paper result tables.
- Add a durable cache/database so repeated runs can reuse downloaded raw files.
- Add human review for ambiguous names such as `IBFE`.
