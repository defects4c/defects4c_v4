# Defects4C v2 — Defects4J-Compatible API for C/C++ Bug Benchmarks

A Defects4J-style HTTP service for C/C++ bug reproduction and automated patch validation,
built on top of the proven `bug_helper_v1_out2.py` workflow.

## Quick Start

```bash
# 1. Ensure host mount dirs exist
mkdir -p out_tmp_dirs patche_dirs workspace

# 2. Build and start
docker-compose up -d --build

# 3. Warmup: full-clone cppcheck + reproduce 6 verified bugs (~15 min)
docker exec defects4c_v2_defects4c_1 bash /src/run_warmup_selected.sh

# 4. Verify
python3 http_tutorial.py --list         # show 6 verified bugs
python3 http_tutorial.py --oracle 0     # oracle check (fix=PASS, buggy=FAIL)
python3 http_tutorial.py --bug 0        # full tutorial with LLM
```

## Verified Bugs (6 cppcheck, 2021–2022)

All compile with clang-16 and pass oracle validation (fix→PASS, buggy→FAIL):

| # | SHA | Source | Trigger Filter | Description |
|---|-----|--------|----------------|-------------|
| 0 | caa6ff7c | lib/analyzerinfo.cpp | TestAnalyzerInformation | Control Expression Error |
| 1 | d0b6079a | lib/checkcondition.cpp | TestCondition | Condition logic bug |
| 2 | 398fa280 | lib/valueflow.cpp | TestStl | ValueFlow STL container bug |
| 3 | c4dcfef3 | lib/tokenize.cpp | TestSymbolDatabase | Tokenizer symbol database bug |
| 4 | 4779f0e1 | lib/templatesimplifier.cpp | TestSimplifyTemplate | Template simplifier bug |
| 5 | 192c30ab | lib/tokenize.cpp | TestTokenizer | Tokenizer crash bug |

## Tutorial Workflow

```
Step 0:  Health check
Step 1:  Select bug (from selections.txt produced by warmup)
Step 2:  Get defect metadata (FL info, trigger/regression tests)
Step 3:  Checkout buggy source (git checkout commit_before -- src_file)
Step 4:  Compile (incremental ninja rebuild)
Step 5:  Trigger test (should FAIL on buggy code)
Step 5b: Oracle validation (fix=PASS, buggy=FAIL)
Step 6:  LLM generates patch
Step 7:  Build patch file
Step 8:  Submit fix (async)
Step 9:  Poll for result
Step 10: Regression test (only if fix passed; skipped otherwise)
```

## Key Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Liveness probe |
| `/api/exec` | POST | D4J-style CLI interface |
| `/checkout` | POST | Restore buggy source file |
| `/compile` | POST | cmake + ninja build |
| `/trigger_test` | POST | Run trigger tests only |
| `/regression_test` | POST | Run full test suite |
| `/validate_oracle` | POST | Oracle validation (fix/buggy) |
| `/list_defects_bugid` | GET | List all bug IDs |
| `/get_defect/{id}` | GET | Metadata + FL + prompt |
| `/build_patch` | POST | LLM response → patch file |
| `/fix` | POST | Async patch validation |
| `/status/{handle}` | GET | Poll fix result |

## Architecture

- **Host disk**: `out_tmp_dirs/` holds cloned repos, build artifacts, logs (bind-mounted to `/out`)
- **Container**: web service + compile + test using original `bug_helper_v1_out2.py`
- **Warmup**: full-clones cppcheck once, per-bug local clone, `bug_helper_v1_out2.py reproduce` verifies each bug

See **[guidance.md](guidance.md)** for regression test preparation and extending to new projects.

