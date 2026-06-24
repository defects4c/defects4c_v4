#!/bin/bash
# Comprehensive reproduction script for libgd bugs
# Run inside container: docker compose exec -T defects4c bash /tmp/repro_libgd.sh 2>&1 | tee /tmp/repro_libgd_out.txt
set -euo pipefail

BUG=$1  # 1846f48e, 58b6dde, 69d2fd2c, fd623025
SAN=$2   # asan, ubsan, asan+ubsan, vanilla
MODE=$3  # buggy or fix

echo "=== BUILDING: bug=$BUG SAN=$SAN MODE=$MODE ==="
BASEDIR=/out/libgd___libgd

# Map bug IDs to commit SHAs
case $BUG in
  1846f48e)
    BUGGY="58b6dde319c301b0eae27d12e2a659e067d80558"
    FIX="1846f48e5fcdde996e7c27a4bbac5d0aef183e4b"
    TEST="gdimagecreate/bug00340"
    TESTFILE="tests/gdimagecreate/bug00340.c"
    ;;
  58b6dde)
    BUGGY="fe9ed49dafa993e3af96b6a5a589efeea9bfb36f"
    FIX="58b6dde319c301b0eae27d12e2a659e067d80558"
    TEST="tga/heap_overflow"
    TESTFILE="tests/tga/heap_overflow.c"
    ;;
  69d2fd2c)
    BUGGY="1846f48e5fcdde996e7c27a4bbac5d0aef183e4b"
    FIX="69d2fd2c597ffc0c217de1238b9bf4d4bceba8e6"
    TEST="gd2/bug00354"
    TESTFILE="tests/gd2/bug00354.c"
    ;;
  fd623025)
    BUGGY="1ccfe21e14c4d18336f9da8515cd17db88c3de61"
    FIX="fd623025505e87bba7ec8555eeb72dae4fb0afdc"
    TEST="gdimagecrop/gd_crop_test"
    TESTFILE=""
    ;;
  *)
    echo "Unknown bug: $BUG"
    exit 1
    ;;
esac

if [ "$MODE" = "buggy" ]; then
  TARGET_COMMIT=$BUGGY
else
  TARGET_COMMIT=$FIX
fi

# Find the _gittree that contains this commit
GITTREE=""
for d in $BASEDIR/_gittree_*; do
  if git -C "$d" cat-file -e "$TARGET_COMMIT" 2>/dev/null; then
    GITTREE="$d"
    break
  fi
done

if [ -z "$GITTREE" ]; then
  echo "ERROR: No _gittree contains commit $TARGET_COMMIT"
  exit 1
fi

echo "Using gittree: $GITTREE"

# Work in a temp directory
WORKDIR="/tmp/libgd_repro_${BUG}_${MODE}_${SAN}"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"

# Clone the source at the target commit
git -C "$GITTREE" archive "$TARGET_COMMIT" | tar x -C "$WORKDIR"
cd "$WORKDIR"

# Set CFLAGS based on sanitizer
case $SAN in
  asan)
    CFLAGS="-fsanitize=address -fno-omit-frame-pointer -g -Wno-error"
    LDFLAGS="-fsanitize=address"
    ;;
  ubsan)
    CFLAGS="-fsanitize=undefined -fno-omit-frame-pointer -g -Wno-error"
    LDFLAGS="-fsanitize=undefined"
    ;;
  asan+ubsan)
    CFLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer -g -Wno-error"
    LDFLAGS="-fsanitize=address,undefined"
    ;;
  vanilla)
    CFLAGS="-g -Wno-error"
    LDFLAGS=""
    ;;
  *)
    echo "Unknown sanitizer: $SAN"
    exit 1
    ;;
esac

echo "CFLAGS=$CFLAGS"

# Bootstrap if needed
if [ -f bootstrap.sh ]; then
  ./bootstrap.sh
fi

# Configure
./configure --enable-static=no --enable-shared=yes --with-zlib --with-png --with-freetype --with-fontconfig --with-jpeg --with-xpm --with-tiff --with-webp CFLAGS="-Wno-error" 2>&1 | tail -5

# Fill in missing tests from parent
rsync -a --ignore-existing --exclude='*.log' --exclude='*.trs' --exclude='*.o' --exclude='.deps/' --exclude='.libs/' ../tests/ tests/ 2>/dev/null || cp -rn ../tests/. tests/ 2>/dev/null || true
find . \( -name 'configure' -o -name 'aclocal.m4' -o -name 'Makefile.in' -o -name 'Makefile' -o -name 'config.status' -o -name '*.h.in' -o -name 'config.h' \) -exec touch {} + 2>/dev/null || true

# Build the library and tests with our sanitizer flags
echo "Building..."
make CFLAGS="${CFLAGS}" CXXFLAGS="${CFLAGS}" LDFLAGS="${LDFLAGS}" AUTOMAKE=: AUTOCONF=: ACLOCAL=: AUTOHEADER=: MAKEINFO=: -j4 2>&1 | tail -5

# Build just the specific test
cd tests
echo "Building test $TEST..."
make CFLAGS="${CFLAGS}" CXXFLAGS="${CFLAGS}" LDFLAGS="${LDFLAGS}" AUTOMAKE=: AUTOCONF=: ACLOCAL=: AUTOHEADER=: MAKEINFO=: -j4 $TEST 2>&1 | tail -10

# Run the test with timeout (prevent OOM hang)
echo "=== Running test: $TEST ==="
ASAN_OPTIONS="allocator_may_return_null=0:abort_on_error=1:detect_leaks=0" \
UBSAN_OPTIONS="abort_on_error=1:print_stacktrace=1" \
timeout 30 ./$TEST 2>&1
RC=$?
echo "EXIT CODE: $RC"

if [ $RC -ne 0 ]; then
  echo "RESULT: FAIL (exit code $RC) - BUG REPRODUCED!"
else
  echo "RESULT: PASS (exit code 0) - Bug not visible"
fi

# Cleanup
cd /
rm -rf "$WORKDIR"

exit $RC
