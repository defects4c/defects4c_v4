# Defects4C — C/C++ Bug Reproduction & Patch Validation Backend

A Defects4J-style HTTP service (FastAPI on port **8095**) for reproducing and
validating fixes to C/C++ bugs. Used by every APR pipeline in
`agent_apr_d4c/*` to checkout, compile, and test patched code.

For Claude Code session breadcrumbs (hard rules, slot architecture, recovery)
see [`.claude/CLAUDE.md`](.claude/CLAUDE.md).

## Quick Start

```bash
# 1. Build & start (auto-detects host UID/GID)
make up

# 2. Verify
curl -s http://127.0.0.1:8095/health     # → {"status":"ok"}

# 3. List bugs
curl -s http://127.0.0.1:8095/list_defects_bugid | jq -r '.[]' | wc -l   # → 114

# 4. Inspect one defect
curl -s http://127.0.0.1:8095/get_defect/danmar___cppcheck@caa6ff7c | jq .
```

To bring the service down: `make down`. To bounce after editing
`defectsc_tpl/`: `make restart`.

## Benchmark scope

| | Value |
|---|---|
| Bug count | **114** across 46 projects |
| Source of truth | `defectsc_tpl/projects/<project>/bugs_list_new.json` |
| Pool slots per bug | 3 (`__s0`, `__s1`, `__s2`) — fcntl-locked |
| Internal container port | 11111 (mapped to host 8095) |
| Workers | 4 gunicorn processes |

## Tutorial Workflow (called by all APR pipelines)

```
1.  Health check                  GET  /health
2.  List defects                  GET  /list_defects_bugid
3.  Get metadata + FL             GET  /get_defect/<project>@<sha>
4.  Reproduce (provision slots)   POST /reproduce            ← async; provisions __s{0,1,2}
5.  Checkout                      POST /checkout             ← reset a slot to buggy commit
6.  Compile                       POST /compile              ← cmake + ninja in slot
7.  Trigger test                  POST /trigger_test         ← FAIL on buggy code
8.  Regression test               POST /regression_test      ← FAIL on buggy, PASS after fix
9.  Async patch validate          POST /fix → GET /status/{handle}
10. Slot release (best-effort)    POST /api/release_slot
```

## Key Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` · `/health/deep` | GET | liveness / readiness probes |
| `/api/exec` · `/api/exec-shell` | POST | D4J-style CLI bridge |
| `/api/upload` · `/api/download` | POST · GET | file transfer to/from container |
| `/checkout` | POST | reset slot to buggy commit |
| `/compile` | POST | cmake + ninja in slot |
| `/trigger_test` | POST | run trigger tests only |
| `/regression_test` | POST | run full test suite |
| `/reproduce` | POST | full clone + build + verify (provisions pool slots) |
| `/api/release_slot` | POST | best-effort flock release |
| `/list_defects_bugid` | GET | list all 114 bug IDs |
| `/get_defect/{id}` | GET | metadata + FL + LLM prompt |
| `/build_patch` | POST | LLM response → patch file |
| `/fix` · `/status/{handle}` | POST · GET | async patch validation |
| `/projects` | GET | list known projects |
| `/validate_oracle` | POST | oracle correctness check |

Full schema: [`openapi.yaml`](openapi.yaml). FastAPI source: `defectsc_tpl/webapp.py`.

## Architecture

```
                     host                                    container
   ┌───────────────────────────────────┐         ┌──────────────────────────────┐
   │  out/                             │  bind   │  /out/                       │
   │    <proj>/git_repo_dir_<sha>/     │ ──────→ │    (regenerated on /reproduce)│
   │      __s0/  __s1/  __s2/          │         │  fcntl.flock on __s{i}.lock  │
   │  defectsc_tpl/                    │  bind   │  /src/  (live source)        │
   │    webapp.py                      │ ──────→ │    gunicorn -w 4             │
   │    bug_helper_v1_out2.py          │         │    listens on :11111         │
   │  patche_dirs/                     │  bind   │  /patches/                   │
   │  workspace/                       │  bind   │  /workspace/                 │
   └───────────────────────────────────┘         └──────────────────────────────┘
                                                       ↑
                                  host:8095  ─────────┘
```

- **`out/`** is **regenerated** by `/reproduce` — never hand-edit.
- **`defectsc_tpl/`** is **live** — edit here and run `make restart` for changes to take effect.

See **[guidance.md](guidance.md)** for adding new projects or extending the regression-test layer.

## Container Management

```bash
make up       # build & start (auto-detects UID/GID into .env)
make down     # stop & remove
make logs     # tail container logs
make restart  # restart after editing defectsc_tpl/
make health   # quick /health probe
make shell    # interactive shell inside the container
make clean    # full teardown (containers + images + build cache)
```

## Slot Lock Recovery

Pool slots use `fcntl.flock` per-gunicorn-worker. If a batch run is killed
mid-flight, slots can stay locked and `/api/release_slot` is unreliable across
workers. **The only reliable recovery is `make restart`**.

```bash
make restart
until curl -s http://127.0.0.1:8095/health | grep -q ok; do sleep 1; done
```

Full explanation: see [`.claude/CLAUDE.md`](.claude/CLAUDE.md) → "Pool slot architecture (and why it's fragile)".

## MD5-Stamped Status Invariant (2026-04-12)

`cmd_puretest` writes status files whose path ends with the MD5 of the patched
file's current contents. `trigger_pass=true` is only honored when the path's
MD5 matches the file just tested. This eliminates stale-cache false positives
and **must not be changed** without coordinated updates to all four
`agent_apr_d4c/*` pipelines.

## Consumers (APR pipelines that depend on this service)

| Pipeline | Folder |
|---|---|
| SWE-Agent (D4C) | `agent_apr_d4c/sweagent_selfcontainedqwen_oai/` |
| Agentless (D4C) | `agent_apr_d4c/agentless_selfcontainedv2/` |
| RepairAgent (D4C) | `agent_apr_d4c/repairagent_selfcontainedqwen/` |
| ReAct (D4C) | `agent_apr_d4c/cot_not_agent_baseline/` |

Each pipeline's `.claude/CLAUDE.md` documents its specific contract with this
service.
