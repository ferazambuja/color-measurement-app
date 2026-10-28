/* Shared UTF-8 metadata validation and JSON text. Original application code.
 * Callers validate native metadata before emitting it; invalid driver text
 * retains its separate lossless byte representation in session.c.
 */
#ifndef CM_NATIVE_TEXT_H
#define CM_NATIVE_TEXT_H

/* Shared bounded identity envelope, including variable PR ASCII report text. */
#define CM_NATIVE_IDENTITY_TEXT_LIMIT 4096
#include <stdio.h>
/* Validate UTF-8 before serialization, including overlong/surrogate rejection. */
static int cm_utf8_text(const char *text, size_t maximum) {
    const unsigned char *p = (const unsigned char *)text;
    size_t i = 0;
    if (!p) return 0;
    while (p[i]) {
        unsigned c = p[i++], count, value, minimum;
        if (i > maximum) return 0;
        if (c < 0x80) continue;
        if (c >= 0xc2 && c <= 0xdf) { count=1; value=c & 0x1f; minimum=0x80; }
        else if (c >= 0xe0 && c <= 0xef) { count=2; value=c & 0x0f; minimum=0x800; }
        else if (c >= 0xf0 && c <= 0xf4) { count=3; value=c & 7; minimum=0x10000; }
        else return 0;
        while (count--) {
            c=p[i++];
            if (i > maximum || (c & 0xc0) != 0x80) return 0;
            value=(value << 6) | (c & 0x3f);
        }
        if (value < minimum || value > 0x10ffff || (value >= 0xd800 && value <= 0xdfff)) return 0;
    }
    return 1;
}
static void cm_utf8_json_string(FILE *out, const char *text) {
    const unsigned char *p = (const unsigned char *)text;
    if (!text) { fputs("null", out); return; }
    fputc('"', out);
    for (; *p; p++) {
        if (*p == '"' || *p == '\\') fprintf(out, "\\%c", *p);
        else if (*p < 0x20) fprintf(out, "\\u%04x", *p);
        else fputc(*p, out);
    }
    fputc('"', out);
}
#endif
