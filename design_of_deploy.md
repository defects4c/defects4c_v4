# Defects4C Deployment Design — Oracle Validation & Cheat Prevention

**Scope:** D4C Docker backend at `defects4c_docker_web4/` (port 8095).
**Goal:** Validate every deployed bug against its dev-fix oracle, and prevent agents from cheating by reading the fix commit out of git history. Two coexisting git trees per bug, with no concurrency conflict.

---

## 0. Compatibility audit — what stays, what moves, what's new

**Job statement.** Two goals only: (a) block the cheat where agents read the dev fix from history, (b) keep every consumer pipeline and existing API behaviour working as-is. **Not** a rewrite. The default answer to "should this change?" is **no**.

### 0.1. Unchanged (full reuse — do NOT touch)

- All bug metadata: `projects_v1/<proj>/project.json`, `projects_v1/<proj>/bugs_list_new.json`, `projects/<proj>/*.jinja`. **Zero edits.**
- The merge logic in `BugsInfo._init_meta_info` (lines 372-385): concat lists, replace scalars. **Zero edits to the merge code.**
- Common templates `common_build_tpl.jinja`, `common_test_tpl.jinja`. **Zero edits** — they don't touch git.
- Patch-flow templates `workflow_cmake_rebuild_tpl.jinja`, `workflow_cmake_compile_test_tpl.jinja`. **Zero edits** — they operate on slot working tree, no git history.
- `run_reproduce.sh`, `run_patch.sh`, `run_warmup_selected.sh`, `run_test_ind.sh`, `run_own.sh`, `run_web.sh`. **Zero edits.**
- `bug_helper_v1_out2.py` entry points: `cmd_reproduce`, `cmd_fix`, `cmd_checkout`, `cmd_compile`, `cmd_puretest`, `cmd_release_slot`, `cmd_info`. **Public signatures unchanged.**
- All API endpoints in `webapp.py`: `/checkout`, `/compile`, `/trigger_test`, `/regression_test`, `/reproduce`, `/fix`, `/status/{handle}`, `/build_patch`, `/api/release_slot`, `/api/exec`, `/api/exec-shell`, `/api/upload`, `/api/download`, `/list_defects_bugid`, `/get_defect/{id}`, `/projects`, `/validate_oracle`, `/health`, `/health/deep`. **Signatures and response shapes unchanged.**
- MD5-stamped status invariant in `cmd_puretest`. **Untouched.**
- Pool-slot fcntl mechanism, slot count (3), slot paths (`__s{i}`, `__s{i}.lock`). **Untouched.**
- Docker layout: `docker-compose.yaml`, `Dockerfile`, host port 8095, mounts. **Untouched.**
- All four consumer pipelines (`sweagent_selfcontainedqwen_oai`, `agentless_selfcontainedv2`, `repairagent_selfcontainedqwen`, `cot_not_agent_baseline`). **Zero changes** — they keep speaking HTTP to the same endpoints.

### 0.2. Surgical edits (5 files, total ~30 lines of diff)

| File | What changes | Lines | Purpose |
|---|---|---|---|
| `out/git_setup.sh` | Reverse the existing `_gittree` migration block; move `.git` OUT of golden at the end of the pipeline. | ~10 | New-bug layout |
| `defectsc_tpl/run_warmup.sh` | Add `git init` + baseline-buggy commit after each slot rsync. | ~4 | Agent git env |
| `defectsc_tpl/projects_v1/workflow_cmake_tpl.jinja` | 3 `git -C {{repo_dir}}` lines → `git --git-dir={{gittree_dir}}/.git --work-tree={{repo_dir}}`. | 3 | Relocated history access |
| `defectsc_tpl/bug_helper_v1_out2.py` | Add `_gittree_dir()` helper; `_restore_slot` uses it ×2; `_init_meta_info` injects `gittree_dir`; `create_pool_slots` adds slot `git init`. | ~10 | Helper consistency |
| `defectsc_tpl/webapp.py` | `validate_oracle` `gp =` swaps one path; optional log line. | ~3 | HTTP layer follows |

`defectsc_tpl/validate_oracle_batch.sh` also gets a `$git_tree` path fix and a hardcoded-fallback-list deletion (~5 lines), bringing the total to **6 files, ~35 lines**. No file deletion. No API rename. No metadata schema change.

### 0.3. New artefacts (only when no existing file does the job)

| New file | Reason existing doesn't suffice |
|---|---|
| `relocate_gittree.py` (host, one-shot) | Migration for the 114 already-warmed bugs; `--dry-run` + `--reverse` for safety. Bash one-liner could do it but lacks dry-run/reverse. |
| `tests/test_relocate_gittree.py` | New tool needs its own test. |
| `tests/test_slot_baseline_commit.py` | New invariant (slot has baseline-buggy HEAD) needs explicit coverage. |
| `tests/test_meta_merge.py` | Locks the existing merge contract + regression-guards the relocated template. The merge logic existed; the test didn't. |
| `tests/test_warmup_template_relocation.py` | End-to-end proof the new git layer works through warmup. |

That's it. **No new endpoint, no new wrapper script, no new schema.** Anything outside this audit is either reuse or a bug in my plan.

---

## 1. Background — what's deployed today

**Per-bug layout** (`out/<project>/git_repo_dir_<sha>/`):

- The directory IS a git repo whose `HEAD = commit_after = sha` (the fix). Running `git show HEAD` prints the entire dev patch.
- `__s0/, __s1/, __s2/` — pool slots, created via `rsync --exclude=.git --exclude=__s* --exclude=*.lock`. Each slot's `.git/` is an empty fresh init (`HEAD = refs/heads/master`, no commits).

**Confirmed cheat (2026-06-21)** — from inside `__s0/`:

```
git -C .. show HEAD -- <src_file>
```

prints the full dev fix. The `_active_locks` discipline doesn't sandbox filesystem paths; any agent calling `/api/exec-shell` reaches this in one line.

**Existing oracle plumbing** — `POST /validate_oracle {bug_id, mode}` (`webapp.py:1406`).

- `mode=fix` → `git --git-dir=<golden>/.git show <after>:<src_file>` into a leased slot → `cmd_puretest` → expect PASS.
- `mode=buggy` → symmetric on `<before>` → expect FAIL.
- `validate_oracle_batch.sh` exists but loops over a hardcoded 3-bug fallback list and uses an inconsistent `_gittree` suffix path that doesn't exist on disk.

---

## 2. Final design — two git trees per bug

```
out/<project>/
  _gittree_<sha>/.git/           ← ORACLE tree (full history; HEAD = commit_after).
                                   Read-only after warmup. Webapp ONLY ever touches it
                                   via explicit `git --git-dir=...`. Sibling of work_dir
                                   so it is NEVER in any slot's upward `.git` search path.

  git_repo_dir_<sha>/            ← work_dir / golden working tree (no .git here).
    __s0/  __s0.lock             ← pool slot 0
      .git/                      ← FRESH agent tree (init'd at slot creation,
                                   baseline commit = buggy state).
    __s1/  __s1.lock
      .git/
    __s2/  __s2.lock
      .git/
```

**One tree per scope.** Oracle for validation, slot's own for the agent's `git status/commit/diff`. No ambiguity, no cross-talk.

---

## 3. Why the two trees cannot conflict — 4 failure modes, all blocked

### 3.1. Git's upward `.git/` search picking the wrong tree

Git walks `cwd` upward looking for `.git/`. From inside `__s0/` it finds `__s0/.git` immediately and stops. If no slot `.git` existed it would continue upward to `git_repo_dir_<sha>/`, then `<project>/`, then `out/`. **`_gittree_<sha>/` is a SIBLING of `git_repo_dir_<sha>/`** — it never appears in that upward walk. An agent's `git status / commit / diff` is structurally incapable of binding to the oracle tree.

### 3.2. Concurrent reads on the oracle tree

The webapp only does `git --git-dir=... show <commit>:<path>` and `git --git-dir=... diff <before> <after>`. Both are read-only; git's object store is concurrency-safe for reads. Multiple slots restoring or validating simultaneously is fine — no locks taken on the oracle tree.

### 3.3. Concurrent agent writes across slots

`__s0/.git`, `__s1/.git`, `__s2/.git` are independent — separate `index`, `HEAD`, `logs`. No shared state. The fcntl flock on `__s{i}.lock` already keeps two clients out of the same slot. Agent commits cannot race.

### 3.4. Slot's `git diff` accidentally including oracle objects

`git diff` operates only on the index/working-tree of the resolved `.git`. As long as the slot's own `.git` exists, the oracle tree is invisible. We never use `--git-dir` ambiguously.

### 3.5. Remaining cheat path (not auto-blocked)

`git --git-dir=../_gittree_<sha>/.git log` — requires the agent to *guess the path*. For an LLM agent that's one `ls ..` probe away. The relocation alone isn't enough; see Layer 2 below.

---

## 4. Two-layer defense

### Layer 1 — Relocate `.git` to sibling `_gittree_<sha>/` (essential)

Stops every script and agent that reflexively does `git -C ..` or relies on git's upward search. This is the bulk of the fix and lands first.

### Layer 2 — UID isolation on the oracle tree (recommended)

`chmod -R o-rwx` + `chown root:root` on `_gittree_<sha>/`. The gunicorn worker (root inside container) can still read via `git --git-dir=...`. If the agent shell runs as `D4C_UID` (non-root), this layer cleanly blocks `ls ../_gittree_<sha>/` and `git --git-dir=...` from the slot.

**Open question:** Does the gunicorn worker run as a different UID than the agent shell process? Need to grep `Dockerfile`, `docker-compose.yaml`, `run_web.sh` to confirm. If yes, Layer 2 works as designed. If no, Layer 2 is cosmetic — Layer 1 still lands and we document the caveat in the consumer CLAUDE.md files.

---

## 5. Existing scripts in scope (what they do, and what the relocation changes in each)

Read before touching anything. All paths inside the container; on the host they live under `defectsc_tpl/`.

### 5.1. `out/git_setup.sh` (called once per bug, at bug-registration time)

Clones the GitHub origin into `git_repo_dir_<sha>/`, fetches the needed commits (shallow `--depth 1`), checks out `commit_after`, inits submodules. **Crucial fossil at lines 57-63**: a previous "Migration: if `_gittree` exists, move `.git` back into golden" block — i.e., someone already tried the separation and reverted it. Line 132 even comments "*.git stays in target_dir (NO separation)*".

**Relocation change:** reverse the migration block (move OUT of golden, not back), drop the "no separation" trailer, and emit `_gittree_<sha>/.git` as the final resting place. This is the ONE place we change the on-disk layout invariant; everything else just follows it.

### 5.2. `defectsc_tpl/run_reproduce.sh` (per-bug warmup driver)

Reads the bug list from `projects*/bugs_list_new.json`, skips bugs that already have a `${sha}.log` + `build_${sha}/`, and otherwise calls `python3 /src/bug_helper_v1_out2.py reproduce ${project}@${sha}`. **Touches no git directly** — all git logic is inside `bug_helper`. **Change: none.**

### 5.3. `defectsc_tpl/run_warmup.sh` (parallel warmup orchestrator)

Two stages, both parallel via `xargs -P $cpu_count`:
1. Reproduce every `(project, commit_after)` by calling `run_reproduce.sh`.
2. **Slot creation** (lines 37-61): for each `golden = /out/$project/git_repo_dir_$sha`, `rsync -a --exclude=.git --exclude=__s* --exclude=*.lock "$golden/" "$slot/"` × 3 slots. **NO `git init` in the slot today** — slots have no `.git/` after this script runs unless `bug_helper.acquire_slot` later restores one. That asymmetry is part of what we fix.

**Change:** after each `rsync`, add `git -C "$slot" init -q && git -C "$slot" add -A && git -C "$slot" -c user.email=d4c@local -c user.name=d4c commit -q -m baseline_buggy`. Same lines also need to land in `bug_helper.create_pool_slots` and `bug_helper._restore_slot` for the in-process slot creation path.

### 5.4. `defectsc_tpl/run_patch.sh` (patch-validation discovery driver)

For an incoming `<sha>`, finds project, lists `/patches/<project>/*@${sha}___*` patches, and per patch calls `python3 bug_helper_v1_out2.py fix ${project}@${sha} ${patch_file}`. **Touches no git directly.** **Change: none.**

### 5.5. `defectsc_tpl/bug_helper_v1_out2.py` (the Python helper, the core)

Subcommand entry points at `__main__`: `reproduce`, `fix`, `checkout`, `release_slot`, `compile`, `test`, `info`.

Git-history callsites we touch:
- `_restore_slot` (lines 268-296) — `git -C $golden show $commit_before:$src_file` × 2 places (first-time rsync and re-restore). **Migrate** to `git --git-dir=$gittree_dir/.git show ...` where `gittree_dir = $ROOT_DIR/$project/_gittree_$sha`.
- `cmd_reproduce` (line 535) — `git -C {instance.wrk_git} clean -dfx`. This operates on the working tree, not history. **Safe — no change.**

`BugsInfo.wrk_git` (line 338) defaults to `golden_dir`. Since we no longer keep a `.git` in `golden_dir`, every consumer of `wrk_git` either (a) only uses it as a working-tree path → still works, or (b) used it as a git-dir → must be redirected. Audit confirmed: only the working-tree `git clean` and `_restore_slot` reference it; the latter is migrated above.

**Change:** small. Two `git -C golden show` calls become `git --git-dir=...gittree.../.git show`. Add the slot baseline-commit block to `create_pool_slots` and to the first-time path inside `_restore_slot`.

### 5.6. `defectsc_tpl/validate_oracle_batch.sh` (batch oracle script)

Already uses `git --git-dir=$git_tree --work-tree=$work_dir` (line 119) and was written *expecting* the sibling layout — its `$git_tree` variable points at `${sha}_gittree/.git`, a path that has never existed on disk. So this script has been a no-op-on-paper since it shipped.

**Change:** point `$git_tree` at the new `_gittree_$sha/.git`, drop the hardcoded 3-bug fallback list (pull from `/list_defects_bugid`), and replace its `--mode direct` `$GIT checkout` branch with a call to `/validate_oracle` so there's a single source of truth.

### 5.7. `defectsc_tpl/webapp.py` (HTTP layer, gunicorn)

Endpoint impact is small and concentrated:
- `validate_oracle` (line 1406) — uses `gp = f"git -C {golden_dir}"` (line 1444). **Migrate** to `git --git-dir=$gittree_dir/.git`.
- `/checkout`, `/compile`, `/trigger_test`, `/regression_test`, `/fix`, `/reproduce` — operate on the slot working tree; **no change**.
- `/api/exec-shell`, `/api/exec` — pass-through; **no change**, but their existence is what makes the cheat possible today.

**Critical rule** (from the user requirement): the webapp must end up calling the shell/python scripts, NOT re-implementing the logic inline. So `validate_oracle` should `subprocess` into a thin `oracle_check.sh` that wraps the `git --git-dir=...` + cmd_puretest sequence, and `validate_oracle_batch.sh` calls the same primitive. One implementation, two front-doors.

---

## 5A. Bug metadata model (project.json + bugs_list_new.json + jinja stack)

What the agent ultimately runs is *rendered shell* — `inplace_build.sh`, `inplace_test.sh`, `run_reproduce.sh`, `run_puretest.sh`, `run_patch.sh` — produced by `BugsInfo._build_tpl` (`bug_helper_v1_out2.py:393`) from a stack of jinja templates and a per-bug merged context. Understanding this is required because the warmup template touches git history too.

### 5A.1. Two-level metadata: project defaults + per-bug overrides

**Project level** — `projects_v1/<project>/project.json`. Example (DynamoRIO):

```
{
  "env": ["CC=gcc", "CXX=g++"],
  "c_compile": {
    "build": "ninja",
    "build_flags": ["-DBUILD_TESTS=on"],
    "test": "ctest",
    "test_flags": [],
    "clean": "git"
  }
}
```

**Per-bug level** — one entry of `projects_v1/<project>/bugs_list_new.json`. Keys: `commit_after`, `commit_before`, `commit_date`, `files.{src, test, src0_location}`, `unittest`, `type`, `status`, `c_compile.{build, build_flags, test, test_flags, clean}`, `url`. Example per-bug `c_compile` for DynamoRIO `452c17ab…`:

```
{ "build": null, "build_flags": null, "test_flags": ["code_api\\|api.ir*"] }
```

The `test_flags` value is a `ctest -R` regex — it filters the trigger test to the bug-specific subset.

### 5A.2. Merge order (the actual code, `bug_helper_v1_out2.py:372-385`)

Two behaviours, easy to confuse — clarify here:

**`build_flags`, `test_flags` (lists) — CONCATENATED.**
```
b_flags = project.c_compile.build_flags + bug.c_compile.build_flags
t_flags = project.c_compile.test_flags  + bug.c_compile.test_flags
```
Per-bug values are ADDED on top of project defaults, not replacing them. So a bug saying `test_flags: ["code_api\\|api.ir*"]` ANDs that filter onto the project's `test_flags` (which for DynamoRIO is `[]`, so effectively the bug's filter wins; but for projects with non-empty defaults the bug's flags append).

**`build`, `test`, `clean` (scalars) — REPLACED if non-null and non-empty.**
```
defect_compile = {k: v for k, v in bug.c_compile.items() if v is not None and len(v) > 0}
meta_info = {**meta_project, **system_compile, **defect_compile, **{build_flags: b_flags, test_flags: t_flags}}
```
Order matters: defect-level last for scalars; the recomputed lists overwrite at the very end so the concatenation behaviour is preserved.

**Custom template per project/bug.** If `c_compile.build` (or `.test`) is a `.jinja` path (contains `".jinja"`), `_build_tpl_path()` / `_test_tpl_path()` use it instead of the common templates. Otherwise the project falls back to `projects_v1/common_build_tpl.jinja` / `common_test_tpl.jinja`. Per-project overrides live in `projects_v1/<project>/*.jinja` (e.g., `llvm___llvm-project/test_tpl.jinja`).

### 5A.3. Jinja template stack — what gets rendered into the work dir

The five `inplace_*.sh` / `run_*.sh` scripts that end up inside `wrk_git/` (which is the golden during warmup, or a slot during agent runs):

| Rendered file | Template | Renders into | Reads git history? |
|---|---|---|---|
| `inplace_build.sh` | `common_build_tpl.jinja` (or project override) | `wrk_git/` | No (cmake + ninja only) |
| `inplace_rebuild.sh` | same, `is_rebuild=True` | `wrk_git/` | No |
| `inplace_test.sh` | `common_test_tpl.jinja` (or project override) | `wrk_git/` | No (ctest only) |
| `run_reproduce.sh` | `workflow_cmake_tpl.jinja` | `wrk_git/` (= golden during warmup) | **YES** — `git -C {{repo_dir}} checkout -f {{commit_after/before}} -- {{src_file}}` × 3 lines (lines 13, 14, 29). Compares fix vs buggy → produces `_fix.log` and `_buggy.log`. |
| `run_puretest.sh` | `workflow_cmake_compile_test_tpl.jinja` | `wrk_git/` (= slot at runtime) | No (md5 + rebuild + test only) |
| `run_patch.sh` | `workflow_cmake_rebuild_tpl.jinja` | `wrk_git/` (= slot at runtime) | No (cp patch + rebuild + test only) |

**This means `workflow_cmake_tpl.jinja` is a FIFTH git-history callsite** beyond the four I enumerated in §5.5–§5.7 of the prior draft. After relocation, the three `git -C {{repo_dir}}` lines in that template MUST switch to `git --git-dir={{gittree_dir}}/.git --work-tree={{repo_dir}}`, and `BugsInfo._init_meta_info` must inject `gittree_dir` into `meta_info` so the template can render it.

### 5A.4. Variable names already present in `meta_info`

Available to every template render (from `bug_helper_v1_out2.py:386-391`): `repo_dir` (= `wrk_git`), `log_dir`, `build_dir`, `test_log`, `test_files`, `src_file`, plus everything from `meta_project` + per-bug `c_compile`. Add `gittree_dir` to this dict for the relocation.

### 5A.5. Why this section matters for the deploy redesign

- The warmup pipeline depends on git history reachable from `repo_dir`. Stripping `.git` from golden without updating the template silently breaks every project's warmup (the `_fix.log` / `_buggy.log` would never appear).
- Per-bug `build_flags`/`test_flags` are **concatenated** with project defaults — when oracle-validating, render with the SAME merge logic that warmup uses (call `BugsInfo` to compute, don't re-derive in `oracle_check.sh`). Avoids the trap of test_flags drift between warmup and oracle.
- `oracle_check.sh` (new, Phase 1) does NOT need to know about jinja. It calls `defects4c info` to get `(commit_before, commit_after, src_file)` and dispatches to existing `cmd_puretest` (which uses the already-rendered `run_puretest.sh`). One layer of indirection; no template logic duplicated.

---

## 6. Implementation order — scripts first, full unittests, then HTTP

**Rationale (user directive):** the shell + Python primitives must be proven correct and fully unit-tested *before* we touch the HTTP layer. The webapp.py changes become trivial wrappers that subprocess into the audited scripts — minimum surface area, minimum risk to live agents.

Phases run strictly in order. A failure in any phase blocks the next.

### 6.0. Stop container, snapshot

```
cd /home/wangjian/wj_code/defects4c_dirs/defects4c_docker_web4
make down
tar czf out_before_relocate.tar.gz out/        # ~3 min wall, reversible escape hatch
```

Do **not** run during an active batch. Verify no consumer pipeline has a job in flight: `find /data/wangjian/wj_code/defects4c_dirs/agent_apr_d4c/*/d4c_results -name 'result.json' -newer out_before_relocate.tar.gz` should be empty for 5 minutes.

### 6.1. PHASE 1 — Shell scripts (host-side, container-down)

**Edits (all idempotent, all reversible):**

1. **`out/git_setup.sh`** — invert the `_gittree` migration block; the post-pipeline trailer moves `.git` from `target_dir/.git` to `${target_dir}_gittree/.git`. After edit, the script's invariant becomes: `target_dir/` has source files only, `_gittree_<sha>/.git/` has history.

   Note the path convention shift: the old failed attempt used `git_repo_dir_<sha>_gittree/` (sibling that suffixes the sha-dir name). I prefer `_gittree_<sha>/` (sibling under the project), which is shorter, glob-friendly, and matches what `validate_oracle_batch.sh` half-expects. Pick ONE and commit; the doc uses `_gittree_<sha>/`.

2. **`defectsc_tpl/run_warmup.sh`** — after each slot rsync (lines 52-60), add the slot `git init` + baseline-buggy commit block. Idempotent: guard on `[[ ! -d $slot/.git ]]`.

3. **`relocate_gittree.py`** (new, host-only, one-shot for existing bugs). Migrates every `out/<project>/git_repo_dir_<sha>/.git/` → `out/<project>/_gittree_<sha>/.git/` for bugs that pre-date the `git_setup.sh` rewrite. Idempotent, has a `--reverse` flag, has a `--dry-run` flag. Skips bugs already migrated.

4. **`defectsc_tpl/projects_v1/workflow_cmake_tpl.jinja`** — the warmup template (§5.5.3). Three `git -C {{repo_dir}} checkout -f ...` lines (13, 14, 29) change to `git --git-dir={{gittree_dir}}/.git --work-tree={{repo_dir}} checkout -f ...`. Adds one new jinja variable: `gittree_dir`. This is the fix for the otherwise-silent warmup breakage.

5. **`defectsc_tpl/validate_oracle_batch.sh`** — point `$git_tree` at `_gittree_$sha/.git`; replace hardcoded fallback bug list with `/list_defects_bugid` (or error if the endpoint is down); drop the `--mode direct` branch in favor of `/validate_oracle`.

No new oracle-check script. `/validate_oracle` already exists (`webapp.py:1406`) and does exactly this work — we just point its `gp =` at the new `_gittree_<sha>/.git` path. **Reuse-first principle: don't add a wrapper around an endpoint that's already the wrapper.**

### 6.2. PHASE 2 — Python helper (`bug_helper_v1_out2.py`)

Four surgical edits:

1. **Add `_gittree_dir(project, sha)`** module-level helper:
   ```python
   def _gittree_dir(project, sha):
       return os.path.join(ROOT_DIR, project, f"_gittree_{sha}")
   ```
   Single source of truth for the sibling path convention.

2. **`_restore_slot`** (lines 268-296) — replace both `git -C $golden show ...` calls:
   ```python
   gittree = _gittree_dir(project, sha)
   subprocess.run(["git", f"--git-dir={gittree}/.git", "show", f"{commit_before}:{src_file}"], ...)
   ```

3. **`create_pool_slots`** (lines 303-323) — after the rsync, add the slot init + baseline-buggy commit (factored into `_init_slot_git(slot)` so both `run_warmup.sh` and `_restore_slot` first-time path use the same helper).

4. **`BugsInfo._init_meta_info`** (lines 365-391) — inject `gittree_dir` into `meta_info` so `workflow_cmake_tpl.jinja` can render `git --git-dir={{gittree_dir}}/.git`:
   ```python
   self.meta_info.update({
       "build_dir":   f"build_{sha}",
       "test_log":    os.path.join(self.wrk_log, f"test_{sha}_fix.log"),
       "test_files":  jmespath.search("files.test", self.meta_defect),
       "src_file":    jmespath.search("files.src[0]", self.meta_defect),
       "gittree_dir": _gittree_dir(self.project, sha),   # NEW
   })
   ```
   The merge order (concatenate lists, replace scalars; §5.5.2) is unchanged. The render context now exposes `gittree_dir` to every workflow template, but only `workflow_cmake_tpl.jinja` uses it.

`BugsInfo.wrk_git` keeps pointing at golden during warmup, at the slot during agent runs — unchanged. The split-dir git invocation handles the absent `.git` cleanly. No type change.

### 6.3. PHASE 3 — Unittests (the gate before HTTP changes land)

Extend `defects4c_docker_web4/tests/` (pytest already wired). Two new test files plus extensions to existing ones.

**New: `tests/test_relocate_gittree.py`** — host-only, no container:
- Build a tmp `out_fake/<proj>/git_repo_dir_<sha>/.git/` (init real git repo with a fake commit).
- Run `relocate_gittree.py --root tmp_out_fake`. Assert sibling `_gittree_<sha>/.git/` exists, golden has no `.git`, source files intact, sha files unchanged byte-for-byte.
- Re-run (idempotency): nothing changes.
- `--reverse`: original layout restored byte-for-byte.
- Edge cases: bug with no `.git` (skip), bug with empty `_gittree_<sha>/` already present (merge or skip with clear error — pick "skip with error"), permission denied on `.git` (clear error).

**New: `tests/test_slot_baseline_commit.py`** — container-required:
- Lease a slot via `/checkout` for one bug.
- Assert `__s{i}/.git/` exists and HEAD points to a commit with the baseline message `baseline_buggy`.
- Run `/api/exec-shell` `cmd="echo //t >> some.c && git add -A && git -c user.email=t@t -c user.name=t commit -m t"`. Assert rc=0.
- `git diff HEAD~1` in slot prints the test change. Confirms agent's full git workflow works.

**Extend `tests/test_shell_scripts.py`** — `git_setup.sh` direction tests:
- Run `git_setup.sh <fake_proj> <fake_sha>` against a local-bare-repo URL. Assert end state: `target_dir/` has no `.git`, sibling `_gittree_<sha>/.git/` has the history, `git --git-dir=...gittree.../.git log --oneline -1` succeeds.
- Re-run (idempotency). End state unchanged.
- Reverse-migration smoke (the OLD direction, just to prove the reversed block still moves things correctly when invoked manually for rollback).

**New: `tests/test_meta_merge.py`** — verifies the project + per-bug merge logic (§5.5.2), which the warmup template silently depends on:
- Project `build_flags=["-A"]` + bug `build_flags=["-B"]` → merged `["-A","-B"]` (concatenation, not replacement).
- Project `build_flags=["-A"]` + bug `build_flags=null` → merged `["-A"]` (bug-null is skipped).
- Project `build="ninja"` + bug `build=null` → merged `build="ninja"` (scalar inherit).
- Project `build="ninja"` + bug `build="make"` → merged `build="make"` (scalar override).
- Bug `c_compile.test_flags=["regex"]` → renders into `inplace_test.sh` as `filter_item="regex"`. Asserts via `_build_tpl` + read-back.
- **Regression guard for relocation**: render `run_reproduce.sh` from `workflow_cmake_tpl.jinja` against a `meta_info` containing `gittree_dir`. Grep the rendered output for `git --git-dir=` (3 occurrences expected, jinja lines 13/14/29) and assert NO `git -C ` substring with `{{repo_dir}}` remains. If someone reverts the jinja edit, this test breaks loudly.

**New: `tests/test_warmup_template_relocation.py`** — narrow integration:
- Set up a fake bug with a real local-bare-repo origin (two commits, a "before" and an "after" on one src file).
- Run `git_setup.sh` (host) → assert `.git` lands in `_gittree_<sha>/`, `target_dir/` has no `.git`, has source files.
- Run `BugsInfo(project, sha).set_reproduce_build()` → assert `run_reproduce.sh` is rendered with `git --git-dir=.../_gittree_<sha>/.git --work-tree=.../git_repo_dir_<sha>`.
- `bash run_reproduce.sh` → assert `_fix.log` and `_buggy.log` exist and the trigger test differs between them. End-to-end proof the relocated git layer is wired.

**Extend `tests/test_oracle_validation.py`** — already exists; tighten to:
- Pre-relocation: assert `validate_oracle(mode=fix)` matches via the *new* path. Same for `mode=buggy`.
- Cheat-blocked: from a leased slot, `/api/exec-shell {cmd: "git -C .. log -1", cwd: <slot>}` returns rc≠0 / "not a git repository". This is the key acceptance test for Layer 1.
- Cheat-probed: `/api/exec-shell {cmd: "ls ../_gittree_<sha>/.git", cwd: <slot>}` — record outcome (informational; documents Layer 2 status). Pass either way; just don't silently flip without a doc update.

**Extend `tests/test_webapp_live.py`** — extend the `/checkout → /compile → /trigger_test → /release_slot` happy path to also: after `/release_slot`, re-`/checkout` and assert the slot's `git status` shows clean working tree (the baseline-buggy commit re-baselined correctly).

**Run gate:** `pytest defects4c_docker_web4/tests/ -x -k 'relocate or slot_baseline or shell_scripts or oracle_validation or webapp_live'` must be 100% green before Phase 4 begins. Per-project tests (`test_apache_arrow.py` etc.) stay green automatically if the helper is correct — they're broad regression coverage.

### 6.4. PHASE 4 — HTTP/gunicorn (`webapp.py`)

Only happens if Phase 3 is fully green. Minimal: one path swap, one optional log line.

1. **`validate_oracle` — one-line path change** (`webapp.py:1443-1444`):
   ```python
   # before
   golden_dir = str(repo_dir)
   gp = f"git -C {golden_dir}"
   # after
   gittree_dir = os.path.join(ROOT_DIR, project, f"_gittree_{sha}")
   gp = f"git --git-dir={gittree_dir}/.git"
   ```
   Everything else in `validate_oracle` stays — the `subprocess.run(f"{gp} show ...")` calls work as-is, only the resolved binary path changes. No new subprocess layer, no JSON parsing, no schema change. Endpoint signature unchanged.

2. **Optional: log (don't block) suspicious `/api/exec-shell` commands** containing `_gittree` or `git -C ..` — telemetry for the Layer 2 caveat. Single `log.info(...)` line. **Do NOT block**; that's the false-confidence trap.

### 6.5. PHASE 5 — Restart + acceptance

```
make up
until curl -s http://127.0.0.1:8095/health | grep -q ok; do sleep 1; done
```

Then the acceptance tests in §7 in order.

---

## 7. Why this beats the alternatives

### Plain `git init` per slot (chosen)

- Slot has its own `.git`; history is invisible.
- Agent's `git status/commit/diff` works for completion-marking and patch capture.
- Easy diff-based patch capture matches what SWE-D4C already does manually today.

### `git worktree` of a shared bare oracle (rejected)

- Would inherit oracle refs in every slot → `git log` from slot would print the full fix history → **reintroduces the cheat**.
- Requires rewriting the rsync-based slot creation as `git worktree add` — bigger refactor, larger risk.

### Read-only bind-mount of slot's parent (rejected)

- Heavy container rework.
- Defense in depth at best; Layer 1 + Layer 2 already cover the realistic threat.

### Wrap `/api/exec-shell` to reject paths outside slot (rejected)

- Easily bypassed (env-var indirection, `find /`, absolute paths from `getcwd`).
- False confidence. Don't ship.

---

## 8. Acceptance tests (run in order, stop on failure)

### 8.1. Layer 1 cheat blocked

```
curl :8095/checkout -d '{"bug_id":"apache___arrow@0b4fa2a2bf80bf3a91f9f8f42fe313f78b8a1282"}'
curl :8095/api/exec-shell -d '{"cmd":"git -C .. log -1", "cwd":"<slot>"}'
```

**Expect:** `fatal: not a git repository`. (Pre-fix: prints fix commit and patch.)

### 8.2. Layer 1 sibling probe (Layer 2 status check)

```
curl :8095/api/exec-shell -d '{"cmd":"git --git-dir=../_gittree_<sha>/.git log -1", "cwd":"<slot>"}'
```

**With UID isolation in place:** `Permission denied`.
**Without:** prints fix (documented caveat; Layer 2 follow-up).

### 8.3. Agent commit env works

```
curl :8095/api/exec-shell -d '{
  "cmd":"echo //t >> some.c && git add -A && git -c user.email=a@b -c user.name=a commit -m t && git diff HEAD~1",
  "cwd":"<slot>"
}'
```

**Expect:** rc=0 and a unified diff prints.

### 8.4. Oracle validate, one bug, both modes

```
curl :8095/validate_oracle -d '{"bug_id":"danmar___cppcheck@<sha>","mode":"fix"}'    # matches_expectation=true
curl :8095/validate_oracle -d '{"bug_id":"danmar___cppcheck@<sha>","mode":"buggy"}'  # matches_expectation=true
```

### 8.5. Full sweep

```
bash validate_oracle_batch.sh > oracle_validation.csv
```

Aggregate into `(passed_both / fix_only / buggy_only / neither / skipped)`. Expected baseline: 114 bugs × 2 modes = 228 rows. In-place build projects (php, libgd, sqlite) may legitimately skip if `build_<sha>/` is absent — they use a different build path; not a regression.

---

## 9. Concurrency policy during validation

The webapp's existing `acquire_slot` is per-bug, 3 slots wide. Validating one bug acquires *one* slot; 2 stay free for normal agent traffic.

**Don't destroy in-flight agents:**

- Validate **serially**, one bug at a time.
- Always release slot in `finally`, even on timeout.
- **No `docker compose restart`** during validation — would kill in-flight agent runs.
- `--gentle` flag: poll `/health/deep` between bugs and skip a bug if all 3 slots are currently locked (under active agent use right now). Batch reports SKIPPED; a second sweep picks them up later.

**Output:** `oracle_validation.csv` with columns `bug_id, mode, expected, actual, matches, latency_s, error` (two rows per bug). Final aggregate printed on stdout.

---

## 10. Risk surface & mitigations

| Risk | Mitigation |
|---|---|
| Existing code does `git -C work_dir log` expecting history | Grep `defectsc_tpl/**` for `git -C` and `git --git-dir` before flipping; route any oracle-history callsite through `_gittree_<sha>/.git`. Five callsites confirmed: `_restore_slot` ×2, `validate_oracle`, `validate_oracle_batch.sh`, `workflow_cmake_tpl.jinja` ×3 lines. |
| Per-bug test_flags / build_flags merge logic drifts between warmup and oracle | `oracle_check.sh` calls `BugsInfo` rather than re-deriving the merge — single source of truth in `bug_helper_v1_out2.py:372-385`. New unit test `test_meta_merge.py` locks the concat-lists / replace-scalars contract. |
| Consumer pipeline docs tell agents "you can git log" | Update consumer `.claude/CLAUDE.md` notes; SWE/Agentless/RA/ReAct READMEs. |
| Submodules in cloned repo | Slot working tree has them; fresh init treats them as untracked dirs. No agent currently modifies submodules; if one does, `git submodule` works on the slot's fresh `.git`. |
| Hooks expected | Stay in oracle gittree (moved with `.git`). Slot's fresh `.git/hooks/` is empty — fine. |
| LFS objects | Stay in oracle gittree; slot working tree has materialized files already. |
| Worker UID == agent UID (Layer 2 fails) | Document caveat; agents that deliberately probe siblings are out of scope for the slot-level sandbox. |
| In-place build projects (php/libgd/sqlite) | `cmd_puretest` already handles both cmake and in-place builds (per webapp comment); validate but expect a few SKIP-not-FAIL. |
| Disk usage doubles? | No — moving `.git` from one path to another, not copying. Oracle tree count unchanged. |

---

## 11. Open question (resolve before Layer 2)

**Does gunicorn worker run as a different UID than the agent shell process?**

Files to grep:

- `Dockerfile`
- `docker-compose.yaml` (look for `user:` directive)
- `run_web.sh` (worker boot command)
- `.env` (`D4C_UID`, `D4C_GID`)

If worker is root and agent shell uses `D4C_UID`: Layer 2 chmod works. Land it.
If both are the same UID: Layer 2 is a no-op. Land Layer 1 only and document.

---

## 12. Order of execution (proposed run plan)

This collapses §6 (Implementation order — scripts first) and §8 (Acceptance) into a linear runlist with approval gates.

1. **Pre-flight grep**: enumerate every `git -C` / `git --git-dir` / `.git/` reference in `defectsc_tpl/**`. Confirm Phase 1+2 covers all of them (already audited; only 4 callsites — listed in §5.5 and §5.7).
2. **Resolve UID open question** (§11). Decides whether Layer 2 chmod is in scope.
3. **§6.0** — `make down` + snapshot.
4. **§6.1 PHASE 1** — edit shell scripts; verify each shell edit individually with `bash -n` syntax check and a manual dry-run on one project (`relocate_gittree.py --dry-run --project danmar___cppcheck`).
5. **§6.2 PHASE 2** — edit `bug_helper_v1_out2.py`. Verify with `python3 -c "import bug_helper_v1_out2; bug_helper_v1_out2._gittree_dir('p', 's')"` smoke.
6. **§6.3 PHASE 3** — run unittests host-side (those that don't need the container) first; `make up` minimal container for the container-required tests; iterate until 100% green. **APPROVAL GATE.**
7. **§6.1 step 3** — run `relocate_gittree.py` for real on `out/`. Per-project, observe successes.
8. **§6.4 PHASE 4** — edit `webapp.py`; `bash -n` + `python3 -c "import webapp"` smoke.
9. **§6.5 PHASE 5** — `make up`; `/health` green.
10. **§8.1–§8.4** — one-bug acceptance. **APPROVAL GATE.**
11. **§8.5** — full sweep; aggregate report.
12. **Layer 2** (if in scope): `chmod -R o-rwx _gittree_*/`, redo §8.2.

Approval gates: after step 6 (unittests green) and after step 10 (one-bug smoke), before step 11 (full sweep).

---

## 13. Reversibility

Any step can be rolled back without touching agent pipelines:

- `relocate_gittree.py --reverse` moves `.git` back into `git_repo_dir_<sha>/`.
- Or `tar xzf out_before_relocate.tar.gz` clobbers `out/` wholesale.
- `_gittree_<sha>/` directory naming is unused elsewhere; no consumer parses it.

The only non-reversible side effect would be a bug-list-source change in `validate_oracle_batch.sh` — keep the old hardcoded fallback in a commented-out block for one revision so a quick revert is easy.
