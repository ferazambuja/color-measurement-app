/* Probe: the SpectroScan COMM-error branch of ss_command (ss_imp.c:539-545)
 * and the four-byte decoder's shift (ss_imp.c:314), over the byte-identical
 * spectro/ss_imp.c of ArgyllCMS 3.5.0 or a corrected copy, linked with the exact
 * numsup float converters and a scripted write_read_ex. One case per process so a
 * sanitizer abort is attributable to it. No transport, no instrument. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sa_config.h"
#include "numsup.h"
#include "cgats.h"
#include "xspect.h"
#include "insttypes.h"
#include "conv.h"
#include "icoms.h"
#include "ss.h"
#include "ss_imp.h"
#include "numsup_extract.inc"	/* C89 special helper and pinned IEEE754 converters */

void msec_sleep(unsigned int msec) { (void)msec; }
void ss_add_soreq(struct _ss *p, int rq);

static const char *g_answer = "";
static int g_writes;
static char g_sent[SS_MAX_WR_SIZE + 4];

/* The device's reply, scripted: bytes as the serial layer would deliver them. */
static int scripted_write_read_ex(struct _icoms *ic, char *wbuf, int nwch, char *rbuf,
                                  int bsize, int *bread, char *tc, int ntc, double tout, int frbw) {
	size_t n = strlen(g_answer);
	(void)ic; (void)nwch; (void)tc; (void)ntc; (void)tout; (void)frbw;
	g_writes++;
	strncpy(g_sent, wbuf, sizeof(g_sent) - 1);
	if ((int)n >= bsize) n = (size_t)bsize - 1;
	memcpy(rbuf, g_answer, n);
	rbuf[n] = '\000';
	if (bread != NULL) *bread = (int)n;
	return ICOM_OK;
}

static ss *new_probe_ss(void) {
	ss *p = (ss *)calloc(1, sizeof(ss));
	icoms *ic = (icoms *)calloc(1, sizeof(icoms));
	ic->write_read_ex = scripted_write_read_ex;
	p->icom = ic;
	p->sbufe = &p->_sbuf[SS_MAX_WR_SIZE-2];		/* as new_ss, ss.c:2179-2181 */
	p->rbuf = p->rbufe = &p->_rbuf[0];
	p->snerr = ss_et_NoError;
	return p;
}

int main(int argc, char **argv) {
	const char *kind, *arg;
	char ans[160];
	ss *p;
	if (argc != 3) return 2;
	kind = argv[1]; arg = argv[2];
	p = new_probe_ss();
	snprintf(ans, sizeof(ans), "%s\r\n", arg);
	g_answer = ans;
	if (strcmp(kind, "moveabs") == 0) {			/* a full SpectroScan command, ss_imp.c:1690ff */
		inst_code rv = ss_do_MoveAbsolut(p, (ss_rt)0, 10.0, 10.0);
		printf("{\"case\": \"moveabs\", \"answer\": \"%s\", \"sent\": \"%.*s\", \"inst_code\": \"0x%x\", "
		       "\"inst_class\": \"0x%x\", \"snerr\": \"0x%x\", \"writes\": %d}\n",
		       arg, (int)strcspn(g_sent, "\r\n"), g_sent, (unsigned)rv,
		       (unsigned)(rv & inst_mask), (unsigned)p->snerr, g_writes);
	} else if (strcmp(kind, "command") == 0) {	/* ss_command alone, after a Spectrolino request */
		ss_add_soreq(p, 0x00);
		ss_command(p, 1.0);
		printf("{\"case\": \"command\", \"answer\": \"%s\", \"snerr\": \"0x%x\", \"remaining\": \"%s\"}\n",
		       arg, (unsigned)p->snerr, p->snerr == ss_et_NoError ? p->rbuf : "");
	} else if (strcmp(kind, "double") == 0) {	/* eight hex characters as the device sends them */
		double v;
		char text[40];
		strcpy(p->_rbuf, arg);
		p->rbuf = p->_rbuf; p->rbufe = p->_rbuf + strlen(arg);
		v = ss_sub_double(p);
		/* The composed helper decodes a special as a special; JSON has no token for one. */
		if (isnan(v)) strcpy(text, "\"nan\"");
		else if (isinf(v)) strcpy(text, v < 0 ? "\"-inf\"" : "\"inf\"");
		else snprintf(text, sizeof text, "%.17g", v);
		printf("{\"case\": \"double\", \"hex\": \"%s\", \"value\": %s, \"negative\": %d, \"snerr\": \"0x%x\", \"consumed\": %d}\n",
		       arg, text, !!signbit(v), (unsigned)p->snerr, (int)(p->rbuf - p->_rbuf));
	} else if (strcmp(kind, "int") == 0) {
		unsigned int v;
		strcpy(p->_rbuf, arg);
		p->rbuf = p->_rbuf; p->rbufe = p->_rbuf + strlen(arg);
		v = ss_sub_4(p);
		printf("{\"case\": \"int\", \"hex\": \"%s\", \"value\": %u, \"bits\": \"0x%08x\", \"snerr\": \"0x%x\"}\n",
		       arg, v, (unsigned)v, (unsigned)p->snerr);
	} else
		return 2;
	free(p->icom);
	free(p);
	return 0;
}
