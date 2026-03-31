# Defects4C v2 — Guidance: Regression Tests & Extension

## How the System Works

The core engine is `bug_helper_v1_out2.py` (unchanged from the original Defects4C).
It reads metadata from two files per project:

- `project.json` — repo URL, default compile flags
- `bugs_list_new.json` — per-bug metadata (commits, source files, test filters)

The reproduce workflow (`bug_helper_v1_out2.py reproduce <project>@<sha>`) does:

```
1. git checkout -f <commit_after>              # fix version
2. git checkout -f <commit_after> -- <src_file>
3. cmake + ninja build (inplace_build.sh)
4. ctest with trigger filter (inplace_test.sh) → expect PASS (fix works)
5. git checkout -f <commit_before> -- <src_file>  # buggy source only
6. ninja rebuild (inplace_rebuild.sh)           # incremental, fast
7. ctest with trigger filter                    → expect FAIL (bug present)
```

## Trigger Tests vs Regression Tests

### Trigger Tests

The **trigger test** is the minimal test that exposes the bug. It is already defined
in `bugs_list_new.json` under `c_compile.test_flags`. For example:

```json
{
    "commit_after": "caa6ff7c2a6ef64df53e04701944aaa4712a1915",
    "c_compile": {
        "test_flags": ["TestAnalyzerInformation"]
    }
}
```

When ctest runs with `-R TestAnalyzerInformation`, it runs only that test class.
On buggy code it FAILS; after a correct fix it PASSES.

### Regression Tests

The **regression test** runs ALL tests to ensure a patch doesn't break anything else.
This field was added to `bugs_list_new.json` as `regression_tests`:

```json
{
    "commit_after": "caa6ff7c2a6ef64df53e04701944aaa4712a1915",
    "c_compile": {
        "test_flags": ["TestAnalyzerInformation"]
    },
    "trigger_tests": ["TestAnalyzerInformation"],
    "regression_tests": ["testrunner"]
}
```

For cppcheck, all tests are compiled into a single `testrunner` binary. Running
`ctest` without a `-R` filter (or with `-R testrunner`) executes ALL test classes.

## How to Prepare Regression Tests for cppcheck

### Step 1: Understand the test structure

cppcheck compiles all tests into one binary: `build_<sha>/bin/testrunner`.
Each test class is registered via ctest. The full list of test classes
(from `test/*.cpp` and the vcxproj):

```
TestAnalyzerInformation  TestAssert  TestAstUtils  TestAutoVariables
TestBool  TestBufferOverrun  TestCharVar  TestCheck  TestClass
TestCmdLineParser  TestCondition  TestConstructors  TestCppcheck
TestErrorLogger  TestExceptionSafety  TestFunctions  TestGarbage
TestImportProject  TestIncompleteStatement  TestInternal  TestIO
TestLeakAutoVar  TestLibrary  TestMathLib  TestMemleak  TestNullPointer
TestOptions  TestOther  TestPath  TestPathMatch  TestPlatform
TestPostfixOperator  TestPreprocessor  TestProgramMemory  TestSettings
TestSimplifyTemplate  TestSimplifyTokens  TestSimplifyTypedef
TestSimplifyUsing  TestSizeof  TestStandards  TestStl  TestString
TestSummaries  TestSuppressions  TestSymbolDatabase  TestThreadExecutor
TestTimer  TestToken  TestTokenize  TestTokenList  TestTokenRange
TestType  TestUninitVar  TestUnusedFunctions  TestUnusedPrivFunc
TestUnusedVar  TestUtils  TestVaarg  TestValueFlow  TestVarId
```

### Step 2: Determine what to put in `regression_tests`

For cppcheck, the simplest and most correct approach:

```json
"regression_tests": ["testrunner"]
```

This runs ALL test classes. Since cppcheck compiles everything into one binary,
there's no way to run a subset of unrelated tests separately — `testrunner` IS
the regression suite.

### Step 3: Verify regression tests pass on the fix version

After warmup, you can verify manually:

```bash
# Inside the container, for bug 0:
cd /out/danmar___cppcheck/git_repo_dir_caa6ff7c2a6ef64df53e04701944aaa4712a1915

# Checkout fix source
git checkout -f caa6ff7c2a6ef64df53e04701944aaa4712a1915 -- lib/analyzerinfo.cpp

# Rebuild
bash inplace_rebuild.sh build_caa6ff7c2a6ef64df53e04701944aaa4712a1915 /dev/null

# Run ALL tests (regression)
ctest --test-dir build_caa6ff7c2a6ef64df53e04701944aaa4712a1915 -VV
# Expected: 100% tests passed
```

Or via the API:

```bash
# Oracle fix validation (runs trigger test with fix source → expect PASS)
curl -X POST http://localhost:11111/validate_oracle \
  -H "Content-Type: application/json" \
  -d '{"bug_id": "danmar___cppcheck@caa6ff7c2a6ef64df53e04701944aaa4712a1915", "mode": "fix"}'

# Full regression (runs ALL ctest targets)
curl -X POST http://localhost:11111/regression_test \
  -H "Content-Type: application/json" \
  -d '{"bug_id": "danmar___cppcheck@caa6ff7c2a6ef64df53e04701944aaa4712a1915"}'
```

### Step 4: Update bugs_list_new.json

For each bug in `defectsc_tpl/projects_v1/danmar___cppcheck/bugs_list_new.json`,
ensure these fields exist:

```json
{
    "commit_after": "<fix-sha>",
    "commit_before": "<buggy-sha>",
    "files": {
        "src": ["lib/analyzerinfo.cpp"],
        "test": ["test/testanalyzerinformation.cpp"]
    },
    "c_compile": {
        "test_flags": ["TestAnalyzerInformation"]
    },
    "trigger_tests": ["TestAnalyzerInformation"],
    "regression_tests": ["testrunner"]
}
```

The `trigger_tests` field mirrors `c_compile.test_flags` (used by ctest `-R` filter).
The `regression_tests` field is `["testrunner"]` for cppcheck (runs everything).

## Preparing Regression Tests for Other Projects

For projects that have separate test binaries (not a single testrunner), you need to:

### 1. List available ctest targets

```bash
cd /out/<project>/git_repo_dir_<sha>
ctest --test-dir build_<sha> -N    # list test names without running
```

### 2. Identify which tests are related to the changed source file

For example, if the bug is in `lib/tokenize.cpp`, related tests might include:
`TestTokenize`, `TestTokenList`, `TestSimplifyTokens`, etc.

### 3. Define regression_tests as ALL tests minus the trigger

Or more practically, just use all tests:

```json
"regression_tests": ["<all-test-target>"]
```

### 4. Verify

Run the warmup and check that:
- Fix version: trigger test PASSES, regression tests PASS
- Buggy version: trigger test FAILS

## The Warmup Verification Flow

`run_warmup_selected.sh` does this for each bug:

```
1. git clone --local from full_clone → per-bug repo
2. git checkout -f <commit_after>
3. Verify commit_before is reachable (fallback to parent if not)
4. python3 bug_helper_v1_out2.py reproduce <project>@<sha>
5. Check test_<sha>_fix.status → should be "success"
6. Check test_<sha>_buggy.status → should be "FAILED"
7. If both match → write to selections.txt
```

Only bugs that pass this verification appear in `selections.txt` and are
available to `http_tutorial.py`.

## File Layout

```
defects4c_v2/
├── Dockerfile                          # Ubuntu 22.04 + clang-16 + build tools
├── docker-compose.yaml                 # Bind mounts: out_tmp_dirs, patche_dirs, workspace
├── http_tutorial.py                    # Client tutorial (reads selections.txt)
├── d4c_client.py                       # Drop-in replacement for d4j_client.py
├── selected_bugs.json                  # The 6 bugs with metadata
├── out_tmp_dirs/                       # HOST: repos + builds + logs → /out
│   └── git_setup.sh
├── patche_dirs/                        # HOST: patch files → /patches
├── workspace/                          # HOST: file exchange → /workspace
└── defectsc_tpl/
    ├── bug_helper_v1_out2.py           # ORIGINAL — reproduce + fix controller
    ├── webapp.py                       # FastAPI HTTP service
    ├── config.py                       # Project registry (44 projects)
    ├── run_warmup_selected.sh          # Warmup 6 bugs + verify + selections.txt
    ├── run_web.sh                      # Gunicorn launcher
    ├── run_reproduce.sh                # Reproduce single bug (calls bug_helper)
    ├── run_patch.sh                    # Validate patches (calls bug_helper)
    ├── projects_v1/
    │   ├── common_build_tpl.jinja      # cmake + ninja (-Wno-error)
    │   ├── common_test_tpl.jinja       # ctest runner
    │   ├── workflow_cmake_tpl.jinja    # Reproduce workflow (clang-16)
    │   ├── workflow_cmake_rebuild_tpl.jinja  # Patch validation workflow
    │   └── danmar___cppcheck/
    │       ├── project.json            # Repo URL + default flags
    │       └── bugs_list_new.json      # 31 bugs with trigger + regression tests
    └── projects/                       # v0 projects (autoconf-based)
```

## Adding a New Bug

1. Find the fix commit SHA and its parent (buggy commit)
2. Identify the source file changed and the test that catches the bug
3. Add an entry to `bugs_list_new.json`:

```json
{
    "commit_after": "<fix-sha>",
    "commit_before": "<parent-sha>",
    "files": {
        "src": ["lib/somefile.cpp"],
        "test": ["test/testsomefile.cpp"],
        "src0_location": {
            "line_is_single": true,
            "line_number": 123,
            "hunk_is_single": true,
            "hunk_start": 120,
            "hunk_end": 126,
            "func_is_single": true,
            "func_start": 120,
            "func_end": 126
        }
    },
    "c_compile": {
        "test_flags": ["TestSomeFile"]
    },
    "trigger_tests": ["TestSomeFile"],
    "regression_tests": ["testrunner"]
}
```

4. Add the SHA to `run_warmup_selected.sh` BUGS array
5. Run warmup and verify it appears in `selections.txt`

