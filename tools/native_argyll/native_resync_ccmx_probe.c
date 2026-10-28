/* Source-bound CCMX display-base check.
 * Reads one file with the exact composed read_ccmx() and prints the stored
 * DISPLAY_TYPE_BASE_ID or the reader's error. No instrument or tool runs.
 */
#include <stdio.h>
#include "numsup.h"
#include "xspect.h"
#include "disptechs.h"
#include "ccmx.h"

/* The installed-correction listing is not exercised (xdg_bds.c). */
int xdg_bds(void *er, char ***paths, int type, int access, int scope, char *spec) {
	(void)er; (void)type; (void)access; (void)scope; (void)spec;
	*paths = NULL;
	return 0;
}
void xdg_free(char **paths, int nopaths) { (void)paths; (void)nopaths; }

int main(int argc, char **argv) {
	ccmx *c;
	if (argc != 2 || (c = new_ccmx()) == NULL) return 2;
	if (c->read_ccmx(c, argv[1]) != 0)
		printf("refused %s\n", c->e.m);
	else
		printf("read %d\n", c->cc_cbid);
	c->del(c);
	return 0;
}
