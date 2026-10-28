/* Pure model/mode whole-read floor from the application's PR control owner.
 * No instrument is constructed, initialized or operated.
 * Copyright 2026 Fernando Voltolini de Azambuja. GPL-2.0-or-later.
 */
#include "session.h"
#if defined(CM_CONTRIBUTED_instPR655) || defined(CM_CONTRIBUTED_instPR670)
#include "pr655_control.h"
int main(int argc, char **argv) {
    int i;
    for (i = 1; i + 1 < argc; i += 2) {
        instType dtype = instUnknown;
        char *end;
        unsigned long word;
        errno = 0; word = strtoul(argv[i + 1], &end, 10);
        if (errno || *end || !argv[i + 1][0] || word > 0xffffffffUL) return 2;
#ifdef CM_CONTRIBUTED_instPR655
        if (!strcmp(argv[i], "instPR655")) dtype = instPR655;
#endif
#ifdef CM_CONTRIBUTED_instPR670
        if (!strcmp(argv[i], "instPR670")) dtype = instPR670;
#endif
        if (dtype == instUnknown) return 2;
        printf("%s %lu %.17g\n", argv[i], word, pr655_required_floor_s(dtype, (inst_mode)word));
    }
    return i == argc ? 0 : 2;
}
#endif
