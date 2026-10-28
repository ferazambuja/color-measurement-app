/* Host-only synthetic identifier records for source-extracted Argyll readers.
 * The included definitions are extracted from GPL-2.0-or-later Argyll source;
 * functions.inc includes that source's actual copyright and licence notice.
 * No instrument, transport or complete calibration-cache loader is linked.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>

typedef struct {
    FILE *fp;
    int ef, rd, nbytes;
    unsigned int chsum;
    void *log;
    char *buf;
    size_t bufsz;
} calf;

static jmp_buf fatal;
static int allocations, failures;
static void *tracked_malloc(size_t n) {
    allocations++;
    return n > 2048 ? NULL : malloc(n);
}
static void fake_error(const char *message) {
    (void)message;
    longjmp(fatal, 1);
}
static void sizebuf(calf *x, size_t n) {
    if (n > 2048) fake_error("oversized scratch allocation");
    if (x->bufsz < n) {
        char *buffer = realloc(x->buf, n);
        if (buffer == NULL) fake_error("scratch allocation failed");
        x->buf = buffer;
        x->bufsz = n;
    }
}
static void update_chsum(calf *x, unsigned char *bytes, int n) {
    int i;
    for (i = 0; i < n; i++)
        x->chsum = ((x->chsum << 13) | (x->chsum >> 19)) + bytes[i];
    x->nbytes += n;
}
static void calf_rints2(calf *x, int *value, int n) {
    if (x->ef) return;
    if (fread(value, sizeof(int), n, x->fp) != (size_t)n) x->ef = 1;
    else update_chsum(x, (unsigned char *)value, (int)(sizeof(int) * n));
}
#define a1logd(...) ((void)0)
#define error fake_error
#define malloc tracked_malloc
#include "functions.inc"
#undef malloc

static void check(int condition, const char *name, const char *property) {
    if (!condition) {
        printf("FAIL %s: %s\n", name, property);
        failures++;
    }
}

/* Mutable reader state lives on the heap so it remains defined after longjmp.
 * The controlled allocator turns the pristine negative-length fatal exit into
 * an observable failed refusal, without requesting an enormous allocation.
 */
static void run_case(const char *name, int length, const char *body, int size,
                     int valid, int dummy, int passes, int preallocation_refusal) {
    calf *x = calloc(1, sizeof(calf));
    char **value = calloc(1, sizeof(char *));
    int pass;
    int has_trailer = strcmp(name, "eof-valid") && strcmp(name, "eof-short");
    unsigned char trailer = 0x5a;
    if (x == NULL || value == NULL) exit(2);
    x->fp = tmpfile();
    if (x->fp == NULL) exit(2);
    if (fwrite(&length, sizeof(int), 1, x->fp) != 1 ||
        fwrite(body, 1, size, x->fp) != (size_t)size ||
        (has_trailer && fwrite(&trailer, 1, 1, x->fp) != 1)) exit(2);
    if (dummy) {
        *value = malloc(5);
        if (*value == NULL) exit(2);
        memcpy(*value, "KEEP", 5);
    }
    allocations = 0;
    for (pass = 0; pass < passes; pass++) {
        int raised = 0;
        rewind(x->fp);
        x->ef = 0; x->rd = pass; x->nbytes = 0; x->chsum = 0;
        if (setjmp(fatal) == 0) {
            if (dummy) calf_rstrz(x, value);
            else calf_rstrz2(x, value);
        } else {
            raised = 1;
        }
        check(!raised, name, "untrusted length must not enter fatal allocation");
        check(x->rd == pass, name, "read mode restored");
        check(x->ef == !valid, name, "validity or explicit refusal");
        if (preallocation_refusal)
            check(allocations == 0 && *value == NULL, name, "refuse before allocation");
        if (valid && !raised) {
            calf expected = {0};
            update_chsum(&expected, (unsigned char *)&length, sizeof(int));
            update_chsum(&expected, (unsigned char *)body, size);
            check(x->nbytes == expected.nbytes && x->chsum == expected.chsum,
                  name, "count and checksum preserved");
            check(ftell(x->fp) == (long)(sizeof(int) + size)
                  && fgetc(x->fp) == (has_trailer ? trailer : EOF),
                  name, "reader leaves the next field intact");
            if (dummy) {
                check(memcmp(*value, "KEEP", 5) == 0 && x->buf != NULL &&
                      memcmp(x->buf, body, size) == 0, name, "dummy read preserves caller storage");
            } else {
                check(*value != NULL && memcmp(*value, body, size) == 0,
                      name, "identifier bytes preserved");
                check(allocations == pass + 1, name, "two-pass read replaces its allocation");
            }
        }
    }
    free(*value); free(value); free(x->buf); fclose(x->fp); free(x);
}

int main(void) {
    char cap[1025];
    memset(cap, 'A', sizeof(cap));
    cap[1023] = '\0';
    run_case("valid", 4, "ABC", 4, 1, 0, 1, 0);
    run_case("two-pass", 4, "ABC", 4, 1, 0, 2, 0);
    run_case("cap-valid", 1024, cap, 1024, 1, 0, 1, 0);
    run_case("dummy", 4, "ABC", 4, 1, 1, 1, 0);
    run_case("empty-valid", 1, "", 1, 1, 0, 1, 0);
    run_case("zero", 0, "", 0, 0, 0, 1, 1);
    run_case("negative", -1, "", 0, 0, 0, 1, 1);
    run_case("truncated", 1000, "A", 1, 0, 0, 1, 1);
    run_case("no-terminator", 3, "ABC", 3, 0, 0, 1, 0);
    run_case("embedded-terminator", 4, "A\0B", 4, 0, 0, 1, 0);
    cap[1023] = 'A'; cap[1024] = '\0';
    run_case("over-cap", 1025, cap, 1025, 0, 0, 1, 1);
    run_case("eof-valid", 4, "ABC", 4, 1, 0, 1, 0);
    run_case("eof-short", 4, "AB", 3, 0, 0, 1, 1);
    printf("13 synthetic identifier cases; %d failures\n", failures);
    return failures ? 1 : 0;
}
