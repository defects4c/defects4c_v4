#!/bin/sh -x

# jq is built with AddressSanitizer in this harness; running it under valgrind
# makes valgrind ABORT (SIGABRT/134) on BOTH fix and buggy modes because ASan's
# shadow memory range interleaves with valgrind's mapping. Disable valgrind so
# the test discriminates on jq's own exit status.
NO_VALGRIND=1
export NO_VALGRIND

. "${0%/*}/setup" "$@"

msys=false
mingw=false
case "$(uname -s)" in
MSYS*)  msys=true;;
MINGW*) mingw=true;;
esac

JQ_NO_B=$JQ
JQ="$JQ -b"

# CVE-2023-50268 (GHSA-7hmr-442f-qc8j): the unit allocated for decNumberCompare
# was accidentally removed, causing a stack overflow / OOB when comparing a value
# against a nan. The buggy jq SEGVs (SIGSEGV/139, ASan abort) on `1e1000 > nan`;
# the fixed jq evaluates it cleanly and exits 0. Verified empirically on the
# buggy binary: `1e1000 | . > nan` -> core dump (rc=139).
$VALGRIND $Q $JQ -n '1e1000 | . > nan' >/dev/null
