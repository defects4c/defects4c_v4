// Reproducer for nng bug #1541 (CVE resource-consumption)
// Bug: nni_chunk_insert in src/core/message.c uses memmove(ch->ch_ptr + len, ...)
// instead of memmove(ch->ch_buf + len, ...), causing buffer overflow when
// headroom < len <= (cap - ch_len).
//
// The shipped tests (test_msg_insert_body, test_msg_large) do NOT trigger
// this code path - they either fit in headroom or trigger the grow path.

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <nng/nng.h>

// Helper: allocate a message that will trigger the buggy insert path.
// nng_msg_alloc(msg, 0) allocates cap=64, ch_ptr=ch_buf+32, ch_len=0.
// After appending N bytes: ch_len=N, ch_ptr unchanged at ch_buf+32.
// Insert M bytes where 32 < M and N + M <= 64 triggers the else-if branch.
// Buggy: memmove(ch_ptr+40, ch_ptr, 10) overflows past ch_buf+64.

int test_insert_overflow(void)
{
    nng_msg *msg;
    char data[64];
    int rv;

    memset(data, 'A', sizeof(data));

    rv = nng_msg_alloc(&msg, 0);
    if (rv != 0) {
        printf("FAIL: nng_msg_alloc returned %d\n", rv);
        return 1;
    }

    // Append 10 bytes
    rv = nng_msg_append(msg, "0123456789", 10);
    if (rv != 0) {
        printf("FAIL: nng_msg_append returned %d\n", rv);
        nng_msg_free(msg);
        return 1;
    }
    printf("After append 10: len=%zu cap=%zu body=%p\n",
           nng_msg_len(msg), nng_msg_capacity(msg), nng_msg_body(msg));

    // Insert 40 bytes — this should trigger the else-if branch
    // (headroom=32 < 40, but 10+40=50 <= 64)
    rv = nng_msg_insert(msg, data, 40);
    if (rv != 0) {
        printf("FAIL: nng_msg_insert returned %d\n", rv);
        nng_msg_free(msg);
        return 1;
    }

    printf("After insert 40: len=%zu cap=%zu body=%p\n",
           nng_msg_len(msg), nng_msg_capacity(msg), nng_msg_body(msg));

    // Check that the first 40 bytes are 'A's (from data)
    const unsigned char *body = (const unsigned char *)nng_msg_body(msg);
    size_t len = nng_msg_len(msg);

    printf("Body length: %zu\n", len);
    printf("First 40 bytes are 'A': ");
    int ok = 1;
    for (size_t i = 0; i < 40 && i < len; i++) {
        if (body[i] != 'A') {
            ok = 0;
            printf("NO (byte %zu is 0x%02x)\n", i, body[i]);
            break;
        }
    }
    if (ok && len >= 40) printf("YES\n");

    printf("Last 10 bytes: ");
    for (size_t i = (len > 10 ? len - 10 : 0); i < len; i++) {
        printf("%c", body[i]);
    }
    printf("\n");

    // Also check that the last 10 bytes match "0123456789"
    int match = 1;
    if (len >= 50) {
        for (size_t i = 40; i < 50 && i < len; i++) {
            if (body[i] != "0123456789"[i - 40]) {
                match = 0;
                break;
            }
        }
        printf("Last 10 bytes match '0123456789': %s\n", match ? "YES" : "NO");
    }

    nng_msg_free(msg);
    return 0;
}

int main(void)
{
    printf("=== Reproducer for nng bug #1541 ===\n\n");
    
    int ret = test_insert_overflow();
    
    printf("\n%s\n", ret == 0 ? "PASS (no crash)" : "FAIL");
    return ret;
}
