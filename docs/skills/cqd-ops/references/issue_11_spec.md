# Issue #11 — Remote Backtest Worker on Home PC (spec, NOT yet implemented)

Repo: jjroth89/cqd-trading-bot. Full body in the issue; condensed architecture here.

## Goal
CQD runs on a low-power Oracle VPS. Heavy backtests / Optuna sweeps run on a home PC via a
secure, deterministic remote-execution path with automatic connectivity fallbacks.

## Connectivity tiers (priority order)
1. **Cloudflare Tunnel (recommended)** — `cloudflared` on PC maps `backtest.example.com → localhost:8000`; Cloudflare Access service token. No NAT/port fwd.
2. **Tailscale** — mesh VPN; Tailscale IP / MagicDNS from Oracle → PC.
3. **Reverse SSH (autossh)** — `ssh -N -R 9000:localhost:8000 oracle@oracle-ip`, persisted via systemd `autossh`.
4. **ngrok** — emergency/debug only; never long-term; token + IP allowlist.

## Components to build
- `infra/worker_api.py` (FastAPI): `POST /api/v1/jobs`, `GET /api/v1/jobs/{id}`, `GET /api/v1/results/{id}`. Bearer token auth, rate limit, validation.
- `infra/run_job.sh` (Dockerized): pull pinned `cqd-backtest:sha<commit>`, run `quant_evaluator.py --backtest` + `sandbox_engine.py --replay`, write artifacts.
- `infra/fetch_results.sh`: scp/curl retrieval, optional S3/MinIO sync.
- `Dockerfile` (repo) + pinned image tag per commit.
- `docs/remote_worker_runbook.md`.
- Tests: `tests/test_remote_job_submission.py`, `tests/test_fallbacks.py`; CI `.github/workflows/remote_worker.yml`.

## Job descriptor (JSON)
```json
{ "job_id":"<uuid>", "pairs":["BTC/USDT","ETH/USDT"],
  "from":"2025-01-01T00:00:00Z", "to":"2025-12-31T23:59:00Z",
  "config_path":"s3://bucket/config.json", "random_seed":42,
  "optuna": {"study":"cqd-study","trials":100} }
```

## Artifacts (per job, in results/<job_id>/)
- `trade_log.csv`, `equity_curve.csv`, `backtest_report.json`.
- `backtest_report.json` MUST log `git_commit`, `random_seed`, `docker_image`.
- Determinism requires `--random-seed`, pinned image tag, immutable input data (checksum logged).

## Security checklist (must pass before enabling)
- No live exchange API keys on the worker.
- Bearer token for all API calls; rotate regularly.
- Cloudflare Access or Tailscale ACLs restrict callers.
- Docker resource caps (`--cpus`, `--memory`); run as non-root.
- Audit logs for submissions/retrievals.
- Expose only `/api/v1/jobs` and `/api/v1/results/<id>`.
- Fail-safe: worker rejects jobs requesting live trading or secret access.

## Optuna & parallelism
- Optuna RDB backend (Postgres) on PC for multi-worker trials.
- Multiple worker containers with `--cpus` limits; same study name.
- Cap concurrency to cores; queue backpressure in FastAPI.

## Estimate
2d (1d infra + runner + docs; 1d tests + CI + fallbacks).
