# Bench Analysis Deployment

This document describes how to run the Bench Analysis Workbench as a standalone
Python web service.

## Start locally

```bash
python3 bench_server.py
```

Open:

```text
http://127.0.0.1:8765/
```

## Environment variables

- `HOST`: bind host. Default: `0.0.0.0`
- `PORT` or `BENCH_PORT`: service port. Default: `8765`
- `BENCH_OUTPUT_DIR`: job output directory. Default: `bench_analysis_outputs`

## Render / Railway style deployment

Use this start command:

```bash
python bench_server.py
```

If the platform uses a Procfile, use:

```text
Procfile.bench
```

or copy its command into the platform start-command field.

## Existing VPS deployment

The main public service mounts the Bench Analysis Workbench under:

```text
/bench
```

On the current production domain, the intended public URL is:

```text
https://medai.sugarclaw.top/bench
```

This is the preferred public route because it reuses the existing public service
and does not require exposing a new port.

The deployment script expects a server checkout at:

```text
/opt/content-pipeline
```

It pulls `origin/main`, restarts `server.py`, and can also start the standalone
`bench_server.py` service when `ENABLE_BENCH_SERVER` is not disabled.

Example:

```bash
BENCH_PORT=8765 ./deploy.sh
```

Set this to skip the Bench service during a deploy:

```bash
ENABLE_BENCH_SERVER=0 ./deploy.sh
```

The repository also includes `deploy_bench.sh` when only the Bench service should
be restarted:

```bash
BENCH_PORT=8765 ./deploy_bench.sh
```

## Notes

- Runtime outputs are intentionally not committed to Git:
  `bench_analysis_outputs/` contains SQLite state, raw PDFs, generated reports,
  and cached source files.
- Public users should use the hosted URL. Developers can inspect generated
  reports through the workbench UI.
