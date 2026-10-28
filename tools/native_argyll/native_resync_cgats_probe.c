/* Source-bound CGATS integer-value check.
 * Reads one file with the exact composed cgats.c and prints the outcome: the
 * declared set count and the first set's integer field, the field as a real or
 * string when the parser typed it so, or the parser's error.
 * No instrument or Argyll tool runs.
 */
#include <stdio.h>
#include <stdlib.h>
#include "cgats.h"

/* Count CGATS-owned blocks through its allocator interface. This also exposes
 * a retained keyword on platforms where AddressSanitizer has no leak detector.
 * The allocator itself lives on the stack and is not owned by the CGATS object. */
static int live_blocks;
static void *tracked_malloc(cgatsAlloc *al, size_t size) {
	void *p = malloc(size);
	if (p != NULL) live_blocks++;
	return p;
}
static void *tracked_calloc(cgatsAlloc *al, size_t count, size_t size) {
	void *p = calloc(count, size);
	if (p != NULL) live_blocks++;
	return p;
}
static void *tracked_realloc(cgatsAlloc *al, void *old, size_t size) {
	int was_null = old == NULL;
	void *p = realloc(old, size);
	if (p != NULL && was_null) live_blocks++;
	else if (p == NULL && !was_null && size == 0) live_blocks--;
	return p;
}
static void tracked_free(cgatsAlloc *al, void *p) {
	if (p != NULL) live_blocks--;
	free(p);
}

int main(int argc, char **argv) {
	cgatsAlloc allocator = { tracked_malloc, tracked_calloc, tracked_realloc, tracked_free, NULL };
	cgats *c;
	int rv;
	if (argc != 2) return 2;
	if ((c = new_cgats_al(&allocator)) == NULL) return 2;
	c->add_other(c, "PROBE");	/* as each reader names its own file type */
	rv = c->read_name(c, argv[1]);
	if (rv != 0)
		printf("refused %d %s\n", c->e.c, c->e.m);
	else if (c->ntables < 1 || c->t[0].nsets < 1 || c->t[0].nfields < 2)
		printf("unexpected shape\n");
	else if (c->t[0].ftype[1] == r_t)
		printf("real %g\n", *((double *)c->t[0].fdata[0][1]));
	else if (c->t[0].ftype[1] != i_t)
		printf("string %s\n", (char *)c->t[0].fdata[0][1]);
	else
		printf("read %d %d\n", c->t[0].nsets, *((int *)c->t[0].fdata[0][1]));
	fflush(stdout);
	c->del(c);
	if (live_blocks != 0) {
		fprintf(stderr, "CGATS allocator retained %d block(s)\n", live_blocks);
		return 3;
	}
	return 0;
}
