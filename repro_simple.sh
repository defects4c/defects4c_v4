#!/bin/bash
# Simple reproduction script: build buggy libgd with specified sanitizer, run test
# Usage: docker compose exec -T defects4c bash /tmp/repro_simple.sh <bug_id> <san> <mode>
# <bug_id>: 1846f48e, 58b6dde, 69d2fd2c
# <san>: asan, ubsan, asanubsan (combined), vanilla
# <mode>: buggy, fix

set -e
BUG=$1
SAN=$2
MODE=$3

BASEDIR=/out/libgd___libgd
WORKDIR=/tmp/repro_${BUG}_${MODE}_${SAN}
rm -rf "$WORKDIR"

# Map bug IDs
case $BUG in
  1846f48e) BUGGY=58b6dde319c301b0eae27d12e2a659e067d80558; FIX=1846f48e5fcdde996e7c27a4bbac5d0aef183e4b; TESTNAME=gdimagecreate/bug00340; TESTFILE=tests/gdimagecreate/bug00340.c ;;
  58b6dde)  BUGGY=fe9ed49dafa993e3af96b6a5a589efeea9bfb36f; FIX=58b6dde319c301b0eae27d12e2a659e067d80558; TESTNAME=tga/heap_overflow; TESTFILE=tests/tga/heap_overflow.c ;;
  69d2fd2c) BUGGY=1846f48e5fcdde996e7c27a4bbac5d0aef183e4b; FIX=69d2fd2c597ffc0c217de1238b9bf4d4bceba8e6; TESTNAME=gd2/bug00354; TESTFILE=tests/gd2/bug00354.c ;;
  *) echo "Unknown bug"; exit 1 ;;
esac

TARGET=$BUGGY; [ "$MODE" = "fix" ] && TARGET=$FIX

# Find gittree with this commit
GITTREE=""
for d in $BASEDIR/_gittree_*; do git -C "$d" cat-file -e "$TARGET" 2>/dev/null && { GITTREE="$d"; break; } || true; done
echo "GITTREE=$GITTREE TARGET=$TARGET"

# Extract source at target commit
git -C "$GITTREE" archive "$TARGET" | tar x -C "$WORKDIR"
cd "$WORKDIR"

# Set compiler flags
case $SAN in
  asan)      CFLAGS="-fsanitize=address -fno-omit-frame-pointer -g -Wno-error"; LDFLAGS="-fsanitize=address" ;;
  ubsan)     CFLAGS="-fsanitize=undefined -fno-omit-frame-pointer -g -Wno-error"; LDFLAGS="-fsanitize=undefined" ;;
  asanubsan) CFLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer -g -Wno-error"; LDFLAGS="-fsanitize=address,undefined" ;;
  vanilla)   CFLAGS="-g -Wno-error"; LDFLAGS="" ;;
  *) echo "Unknown san"; exit 1 ;;
esac

echo "CFLAGS=$CFLAGS"

# Bootstrap + configure
[ -f bootstrap.sh ] && ./bootstrap.sh >/dev/null 2>&1
./configure --enable-static=no --enable-shared=yes --with-zlib --with-png --with-freetype --with-fontconfig --with-jpeg --with-xpm --with-tiff --with-webp CFLAGS="-Wno-error" >/dev/null 2>&1

# Fill tests from parent (the _gittree parent dir)
PARENT=$(dirname "$GITTREE")
if [ -d "$PARENT/tests" ]; then
  find "$PARENT/tests" -type f \( -name '*.c' -o -name '*.gd2' -o -name '*.tga' -o -name '*.gif' -o -name '*.png' -o -name '*.jpg' -o -name '*.am' -o -name '*.in' -o -name 'gdtest.h' -o -name 'gdtest.c' \) -exec cp --parents {} "$WORKDIR/" \; 2>/dev/null || true
fi
find . \( -name 'configure' -o -name 'aclocal.m4' -o -name 'Makefile.in' -o -name 'config.h' \) -exec touch {} + 2>/dev/null || true

# Build library
make CFLAGS="${CFLAGS}" CXXFLAGS="${CFLAGS}" LDFLAGS="${LDFLAGS}" AUTOMAKE=: AUTOCONF=: ACLOCAL=: AUTOHEADER=: MAKEINFO=: -j4 2>&1 | tail -3

# Build & run test
cd tests
make CFLAGS="${CFLAGS}" CXXFLAGS="${CFLAGS}" LDFLAGS="${LDFLAGS}" AUTOMAKE=: AUTOCONF=: ACLOCAL=: AUTOHEADER=: MAKEINFO=: -j4 "$TESTNAME" 2>&1 | tail -5

echo "=== RUNNING $TESTNAME ==="
ASAN_OPTIONS="allocator_may_return_null=0:abort_on_error=1:detect_leaks=0" \
UBSAN_OPTIONS="abort_on_error=1:print_stacktrace=1" \
timeout 30 ./$TESTNAME 2>&1
RC=$?
echo "EXIT: $RC"
[ $RC -ne 0 ] && echo "RESULT: FAIL" || echo "RESULT: PASS"
exit $RC
