# Judge0 (execution engine) — deployment

This directory holds the deployment configuration for [Judge0](https://judge0.com),
the sandboxed code-execution engine that grades submissions.

You do **not** need the full Judge0 source to run the platform — the stack uses
the prebuilt `judge0/judge0:latest` image. Only these files are tracked in git:

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Runs server + worker + postgres + redis |
| `judge0.conf` | Judge0 configuration (passwords, limits, features) |
| `DEPLOY.md` | This file |

## Run

```bash
docker compose up -d
curl http://localhost:2358/about   # health check
```

## Notes

- **Secrets:** `judge0.conf` ships with dev-only `REDIS_PASSWORD` / `POSTGRES_PASSWORD`.
  Change them before any non-local deployment.
- **cgroup v2 hosts:** Judge0 1.13.1's `isolate` cannot use cgroups on cgroup-v2
  hosts. The backend works around this per-submission — see
  [`../docs/troubleshooting.md`](../docs/troubleshooting.md).
- The full upstream source is git-ignored. To fetch it for reference:
  `git clone https://github.com/judge0/judge0`.
