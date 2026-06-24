#!/bin/bash
# Rigorous reproduction: instrument the LIBRARY itself (configure CFLAGS), clean build.
# Usage: repro_libgd2.sh <BUG> <SAN> <MODE>
#   BUG: 1846f48e | 58b6dde | 69d2fd2c | fd623025
#   SAN: vanilla | asan | ubsan | asan_ubsan | valgrind
#   MODE: buggy | fix
set -uo pipefail

BUG=$1; SAN=$2; MODE=$3
BASEDIR=/out/libgd___libgd

case $BUG in
  1846f48e) BUGGY=58b6dde319c301b0eae27d12e2a659e067d80558; FIX=1846f48e5fcdde996e7c27a4bbac5d0aef183e4b; TEST="gdimagecreate/bug00340";;
  58b6dde)  BUGGY=fe9ed49dafa993e3af96b6a5a589efeea9bfb36f; FIX=58b6dde319c301b0eae27d12e2a659e067d80558; TEST="tga/heap_overflow";;
  69d2fd2c) BUGGY=1846f48e5fcdde996e7c27a4bbac5d0aef183e4b; FIX=69d2fd2c597ffc0c217de1238b9bf4d4bceba8e6; TEST="gd2/bug00354";;
  fd623025) BUGGY=3fe0a7128bac5000fdcfab888bd2a75ec0c9447d; FIX=fd623025505e87bba7ec8555eeb72dae4fb0afdc;  TEST="";;
  *) echo "unknown bug"; exit 2;;
esac

if [ "$MODE" = buggy ]; then COMMIT=$BUGGY; else COMMIT=$FIX; fi

# pick gittree that has FIX commit (it always has both fix + loose before object)
GITTREE=""
for d in $BASEDIR/_gittree_*; do
  if git -C "$d" cat-file -e "$FIX" 2>/dev/null && git -C "$d" cat-file -e "$COMMIT" 2>/dev/null; then GITTREE="$d"; break; fi
done
[ -z "$GITTREE" ] && { echo "ERR: no gittree has $COMMIT"; exit 3; }
echo "gittree=$GITTREE commit=$COMMIT ($MODE)"

case $SAN in
  vanilla)    SF="";;
  asan)       SF="-fsanitize=address -fno-omit-frame-pointer";;
  ubsan)      SF="-fsanitize=undefined -fno-sanitize-recover=all -fno-omit-frame-pointer";;
  asan_ubsan) SF="-fsanitize=address,undefined -fno-sanitize-recover=undefined -fno-omit-frame-pointer";;
  valgrind)   SF="";;
  *) echo "unknown san"; exit 2;;
esac
CF="$SF -g -O1 -Wno-error"

W=/tmp/lg_${BUG}_${MODE}_${SAN}
rm -rf "$W"; mkdir -p "$W"
git -C "$GITTREE" archive "$COMMIT" | tar x -C "$W"
cd "$W"
# fill in test files (PoC inputs / driver) from the FIX tree if missing in buggy tree
git -C "$GITTREE" archive "$FIX" | tar x -C "$W" --skip-old-files 2>/dev/null || true

[ -f bootstrap.sh ] && ./bootstrap.sh >/tmp/boot_$BUG.log 2>&1
# configure with sanitizer CFLAGS so the LIBRARY is instrumented
./configure --enable-static=no --enable-shared=yes CFLAGS="$CF" LDFLAGS="$SF" >/tmp/conf_$BUG.log 2>&1
echo "configure rc=$?"

make -j4 CFLAGS="$CF" LDFLAGS="$SF" >/tmp/mk_$BUG.log 2>&1
echo "lib make rc=$? (tail:)"; tail -3 /tmp/mk_$BUG.log

# confirm gd source object actually built with sanitizer
echo "--- verify instrumentation of target src object ---"
case $BUG in
  58b6dde) OBJ=src/.libs/libgd_la-gd_tga.o;;
  69d2fd2c) OBJ=src/.libs/libgd_la-gd_gd2.o;;
  1846f48e) OBJ=src/.libs/libgd_la-gd.o;;
  *) OBJ="";;
esac
if [ -n "$OBJ" ] && [ -f "$OBJ" ]; then
  if [ "$SAN" = asan ] || [ "$SAN" = asan_ubsan ]; then nm "$OBJ" 2>/dev/null | grep -q asan && echo "ASAN symbols present in $OBJ" || echo "WARN: no asan symbols in $OBJ"; fi
  if [ "$SAN" = ubsan ]; then nm "$OBJ" 2>/dev/null | grep -qi ubsan && echo "UBSAN symbols present" || echo "WARN: no ubsan symbols in $OBJ"; fi
fi

cd tests
make -j4 CFLAGS="$CF" LDFLAGS="$SF" "$TEST" >/tmp/mkt_$BUG.log 2>&1
echo "test make rc=$? (tail:)"; tail -4 /tmp/mkt_$BUG.log

echo "=== RUN $TEST ($SAN/$MODE) ==="
export ASAN_OPTIONS="allocator_may_return_null=0:abort_on_error=1:detect_leaks=0:halt_on_error=1"
export UBSAN_OPTIONS="abort_on_error=1:print_stacktrace=1:halt_on_error=1"
if [ "$SAN" = valgrind ]; then
  timeout 60 valgrind --error-exitcode=1 --leak-check=no ./$TEST 2>&1 | tail -40; RC=${PIPESTATUS[0]}
else
  timeout 60 ./$TEST; RC=$?
fi
echo "EXIT CODE: $RC"
[ $RC -ne 0 ] && echo "RESULT: FAIL (rc=$RC)" || echo "RESULT: PASS (rc=0)"
echo "WORKDIR kept: $W"
exit $RC
