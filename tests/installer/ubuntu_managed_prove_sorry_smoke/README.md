# Ubuntu Managed `/prove` Staging Smoke

This scenario verifies that a clean `ubuntu:24.04` container can:

1. install Open Gauss from the current checkout with `./scripts/install-internal.sh`
2. copy a tiny Lean project that contains one `sorry`
3. initialize a local `.gauss` manifest in that project
4. stage a managed `/prove HelloSorry/Basic.lean` launch against that project
5. confirm the staged skill, startup context, backend instructions, and MCP
   config all point at the tiny Lean project correctly

Provider requirements:

- set `ANTHROPIC_API_KEY` to exercise the Claude-managed backend, or
- set `OPENAI_API_KEY` to exercise the Codex-managed backend

Usage:

```bash
tests/installer/ubuntu_managed_prove_sorry_smoke/run.sh
```

Notes:

- The scenario mounts the current checkout at `/src`, so it always exercises
  the branch you are testing without needing GitHub access inside the container.
- The default path is deterministic: it validates managed staging only, not a
  full model-driven proof attempt.
- Set `LIVE_MANAGED_PROVE_SMOKE=1` to turn on the best-effort live backend
  execution path for manual debugging. That path is intentionally not the
  default smoke because backend/model timing can make it flaky.
- The fixture intentionally stays tiny so the smoke focuses on managed proving,
  not on a large project setup.
