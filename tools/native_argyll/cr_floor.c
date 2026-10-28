/* The CR-family whole-read floor, for the catalog.
 *
 * argv: pairs of <instType enum name> <inst_mode word>. Prints one line
 * "<enum> <word> <seconds>" per pair, from cr250_required_floor_s: the least
 * capture.required_s any read of that type in that mode can have, or -1 for a mode
 * the row refuses. That function is this application's, composed onto the driver
 * by cr250_host.py. argyll_native_check.py publishes the least positive value per
 * type, so the host's bind floor is computed once, in the composed driver, and
 * never copied here. Linked with the persona's substituted sources: no transport, nothing
 * that opens a port, and the function itself performs no I/O. */
#include "session.h"
#if defined(CM_CONTRIBUTED_instCR250) || defined(CM_CONTRIBUTED_instCR100) || defined(CM_CONTRIBUTED_instCR300)
#include "cr250.h"

FILE *wire;   /* session.h's; the persona sources reference none of it */

int main(int argc, char **argv) {
    int i;
    for (i = 1; i + 1 < argc; i += 2) {
        instType dtype = instUnknown;
        char *end;
        unsigned long word = strtoul(argv[i + 1], &end, 10);
        if (*end || !argv[i + 1][0]) return 2;
#ifdef CM_CONTRIBUTED_instCR250
        if (!strcmp(argv[i], "instCR250")) dtype = instCR250;
#endif
#ifdef CM_CONTRIBUTED_instCR300
        if (!strcmp(argv[i], "instCR300")) dtype = instCR300;
#endif
#ifdef CM_CONTRIBUTED_instCR100
        if (!strcmp(argv[i], "instCR100")) dtype = instCR100;
#endif
        if (dtype == instUnknown) return 2;
        printf("%s %lu %.17g\n", argv[i], word,
               cr250_required_floor_s(dtype, (inst_mode)word));
    }
    return i == argc ? 0 : 2;
}
#endif
