# Live Sync Benchmark

Issue `#51` requires a live local benchmark for every sync-affecting PR. This harness measures real `gh` usage against a disposable agendum workspace so the user's normal `~/.agendum/agendum.db` is never touched.

## What it does

- creates a temporary workspace root
- writes a temp `config.toml`
- copies local `gh` auth into a temp `gh` config dir
- initializes a disposable SQLite DB
- runs a cold sync and then a warm sync
- records wall time and the sync result tuple
- instruments `agendum.gh._run_gh` to count CLI calls and payload bytes

## Current output fields

Each cold and warm phase records:

- `wall_time_s`
- `changes`
- `attention`
- `error`
- `active_task_count`
- `total_gh_calls`
- `payload_bytes`
- `call_classification`
- `payload_bytes_by_classification`
- `lane_pagination_counts`
- `hydration_batch_sizes`
- `sync_metrics_log_surface`
- `calls`

`hydration_batch_sizes` and `sync_metrics_log_surface` are future-facing fields for later issue-51 slices. On the current hot path they will usually be empty.

## Usage

Benchmark an org-backed workspace:

```bash
uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/before.json
```

Benchmark explicit repos only:

```bash
uv run python scripts/live_sync_bench.py \
  --repos owner/repo-a owner/repo-b \
  --exclude-repos owner/repo-b \
  --runs 2 \
  --output /tmp/after.json
```

Compare two reports:

```bash
uv run python scripts/compare_live_sync_bench.py /tmp/before.json /tmp/after.json
```

## Notes

- The harness requires `gh` to already be authenticated locally.
- It uses copied auth in the temp workspace rather than the live workspace directly.
- It does not mutate `~/.agendum/agendum.db`.
- The current `main` baseline still includes repo fanout and per-review detail fetches; the harness is intended to expose that honestly for before/after comparisons.
