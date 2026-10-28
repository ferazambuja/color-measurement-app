/* Shared catalogue over inst.get_disptypesel (Argyll 3.5.0 inst.h).
 * No selector letters, guessed current index, device-name table or file reread.
 * Fingerprints cover the actual loaded matrix/spectra and their list metadata.
 * The list remains owned by the driver. get_disptechi is separate readback.
 */
#ifndef CM_DISPLAY_TYPES_H
#define CM_DISPLAY_TYPES_H
#include <limits.h>
#define CM_DISPLAY_LIMIT 1000

typedef struct {
    inst_disptypesel *rows;
    int count;
    inst_code code;
    char digest[65];
} cm_display_catalogue;

#ifdef CM_DISPLAY_IMPLEMENTATION
#include "cm_display_sha256.h"
#include "native_text.h"
static void cm_display_hash_text(SHA256_CTX *hash, const char *text) {
    char length[32];
    sprintf(length, "%lu:", (unsigned long)strlen(text));
    SHA256_Update(hash, (const uint8_t *)length, (int)strlen(length));
    SHA256_Update(hash, (const uint8_t *)text, (int)strlen(text));
}
static void cm_display_hash_int(SHA256_CTX *hash, int value) {
    char text[32]; sprintf(text, "%d", value); cm_display_hash_text(hash, text);
}
static int cm_display_hash_double(SHA256_CTX *hash, double value) {
    char text[64];
    if (!isfinite(value)) return 0;
    sprintf(text, "%.17g", value); cm_display_hash_text(hash, text); return 1;
}
static void cm_display_hash_finish(SHA256_CTX *hash, char output[65]) {
    unsigned char digest[32];
    SHA256_Final(digest, hash);
    for (int i=0; i<32; i++) sprintf(output + 2*i, "%02x", digest[i]);
    output[64]=0;
}
/* Metadata remains a bounded native C string even when its encoding is unknown.
 * Hash those exact bytes; UTF-8 validity controls selection, not list identity. */
static int cm_display_bounded_text(const char *text, size_t maximum) {
    if (!text || !text[0]) return 0;
    for (size_t i=0; i<=maximum; i++) if (!text[i]) return 1;
    return 0;
}
static const char *cm_display_text_reason(const inst_disptypesel *row) {
    int label=cm_utf8_text(row->desc,sizeof(row->desc)-1);
    int path=!row->path || cm_utf8_text(row->path,1024);
    if (!label && !path) return "Native label and source path are not valid UTF-8";
    if (!label) return "Native label is not valid UTF-8";
    if (!path) return "Native source path is not valid UTF-8";
    return NULL;
}
static void cm_display_hex(FILE *out, const char *text) {
    const unsigned char *p=(const unsigned char *)text;
    fputc('"',out);
    for (; *p; p++) fprintf(out,"%02x",*p);
    fputc('"',out);
}
/* Include only the published classification flags; ld is transient load state.
 * Matrix entries include their base id and all nine coefficients. CCSS entries
 * include each raw xspect grid, norm and sample, not a filename-only checksum. */
static int cm_display_identity(inst_disptypesel *row, int index, char output[65]) {
    SHA256_CTX hash; SHA256_Init(&hash);
    if (!cm_display_bounded_text(row->desc,sizeof(row->desc)-1)
        || (row->path && !cm_display_bounded_text(row->path,1024))) return 0;
    cm_display_hash_text(&hash, "argyll-display-type-v1");
    cm_display_hash_int(&hash, index);
    cm_display_hash_text(&hash, row->desc);
    cm_display_hash_int(&hash, row->flags & ~inst_dtflags_ld);
    cm_display_hash_int(&hash, row->cbid);
    cm_display_hash_int(&hash, row->dtech);
    cm_display_hash_int(&hash, row->refr);
    cm_display_hash_int(&hash, row->ix);
    cm_display_hash_text(&hash, row->path ? row->path : "");
    if (row->flags & inst_dtflags_ccmx) {
        cm_display_hash_int(&hash, row->cc_cbid);
        for (int i=0; i<3; i++) for (int j=0; j<3; j++)
            if (!cm_display_hash_double(&hash, row->mat[i][j])) return 0;
    }
    if (row->flags & inst_dtflags_ccss) {
        if (!row->sets || row->no_sets<1 || row->no_sets>1000) return 0;
        cm_display_hash_int(&hash, row->no_sets);
        for (int i=0; i<row->no_sets; i++) {
            xspect *s=&row->sets[i];
            if (s->spec_n<2 || s->spec_n>XSPECT_MAX_BANDS) return 0;
            cm_display_hash_int(&hash, s->spec_n);
            if (!cm_display_hash_double(&hash,s->spec_wl_short)
                || !cm_display_hash_double(&hash,s->spec_wl_long)
                || !cm_display_hash_double(&hash,s->norm)) return 0;
            for (int j=0; j<s->spec_n; j++)
                if (!cm_display_hash_double(&hash,s->spec[j])) return 0;
        }
    }
    cm_display_hash_finish(&hash,output); return 1;
}
static void cm_collect_display_types(inst *meter, inst2_capability cap,
                                     cm_display_catalogue *catalogue) {
    SHA256_CTX hash;
    memset(catalogue,0,sizeof(*catalogue)); catalogue->code=inst_unsupported;
    if (!(cap & inst2_disptype) || !meter->get_disptypesel) return;
    catalogue->code=meter->get_disptypesel(meter,&catalogue->count,&catalogue->rows,1,1);
    if (catalogue->code!=inst_ok) { catalogue->count=0; return; }
    if (catalogue->count<0 || catalogue->count>CM_DISPLAY_LIMIT
        || (catalogue->count && !catalogue->rows)) {
        catalogue->code=inst_internal_error; catalogue->count=0; return;
    }
    SHA256_Init(&hash); cm_display_hash_text(&hash,"argyll-display-catalogue-v1");
    cm_display_hash_int(&hash,meter->dtype);
    cm_display_hash_int(&hash,catalogue->count);
    for (int i=0; i<catalogue->count; i++) {
        char identity[65];
        if (!cm_display_identity(&catalogue->rows[i],i,identity)) {
            catalogue->code=inst_internal_error; catalogue->count=0; return;
        }
        cm_display_hash_text(&hash,identity);
    }
    cm_display_hash_finish(&hash,catalogue->digest);
}
static void cm_emit_display_types(FILE *out, cm_display_catalogue *catalogue,
                                 void (*start_event)(const char *),
                                 void (*write_string)(const char *)) {
    for (int i=0; i<catalogue->count; i++) {
        inst_disptypesel *row=&catalogue->rows[i]; char identity[65];
        int label=cm_utf8_text(row->desc,sizeof(row->desc)-1);
        int path=!row->path || cm_utf8_text(row->path,1024);
        const char *reason=cm_display_text_reason(row);
        cm_display_identity(row,i,identity);
        start_event("display_type");
        fprintf(out,",\"index\":%d,\"id\":",i); write_string(identity);
        fputs(",\"label\":",out); cm_utf8_json_string(out,label ? row->desc:NULL);
        fprintf(out,",\"available\":%s,\"unavailable_reason\":",reason ? "false":"true");
        write_string(reason);
        fprintf(out,",\"native_default\":%s",
                row->flags & inst_dtflags_default ? "true":"false");
        fputs(",\"native_label_bytes\":",out);
        if (label) fputs("null",out); else cm_display_hex(out,row->desc);
        fputs(",\"native_source_path_bytes\":",out);
        if (path) fputs("null",out); else cm_display_hex(out,row->path);
        fprintf(out,",\"technology_code\":%u,\"refresh_mode\":%d,\"base_calibration_id\":%d,\"kind\":",
                (unsigned)row->dtech,row->refr,row->cbid);
        write_string(row->flags & inst_dtflags_ccss ? "ccss" :
                     row->flags & inst_dtflags_ccmx ? "ccmx" :
                     row->flags & inst_dtflags_mtx ? "instrument_matrix" : "builtin");
        fputs(",\"identity_scope\":",out);
        write_string(row->flags & (inst_dtflags_ccmx | inst_dtflags_ccss)
                     ? "loaded_correction_data" : "catalogue_metadata");
        fputs(",\"source_path\":",out); cm_utf8_json_string(out,path ? row->path:NULL);
        fprintf(out,",\"custom\":%s}\n",row->flags & inst_dtflags_custom ? "true":"false");
    }
}
static void cm_print_display_types(FILE *out, const cm_display_catalogue *catalogue,
                                  inst *meter, int selected,
                                  void (*write_string)(const char *)) {
    disptech technology=(disptech)INT_MIN; int refresh=INT_MIN,base=INT_MIN;
    inst_code rv=inst_unsupported;
    char identity[65];
    if (catalogue->code==inst_ok && meter->get_disptechi)
        rv=meter->get_disptechi(meter,&technology,&refresh,&base);
    fprintf(out,",\"display_types\":{\"status\":\"%s\",\"native_code\":%u,\"count\":%d,\"catalogue_id\":",
            catalogue->code==inst_ok ? "available" :
            (catalogue->code & inst_mask)==inst_unsupported ? "unsupported" : "failed",
            (unsigned)catalogue->code,catalogue->count);
    write_string(catalogue->code==inst_ok ? catalogue->digest:NULL);
    fputs(",\"applied_choice_id\":",out);
    if (selected>=0 && selected<catalogue->count) {
        cm_display_identity(&catalogue->rows[selected],selected,identity); write_string(identity);
    } else write_string(NULL);
    fprintf(out,",\"readback\":{\"native_code\":%u,\"technology_code\":",(unsigned)rv);
    if (rv==inst_ok && technology!=(disptech)INT_MIN) fprintf(out,"%u",(unsigned)technology);
    else fputs("null",out);
    fputs(",\"refresh_mode\":",out);
    if (rv==inst_ok && refresh!=INT_MIN) fprintf(out,"%d",refresh); else fputs("null",out);
    fputs(",\"base_calibration_id\":",out);
    if (rv==inst_ok && base!=INT_MIN) fprintf(out,"%d",base); else fputs("null",out);
    fputs("}}",out);
}
#endif /* CM_DISPLAY_IMPLEMENTATION */
#endif
