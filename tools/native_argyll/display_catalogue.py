"""Reuse pinned axTLS SHA-256 for fingerprints of loaded Argyll display lists.

Only the includes and linkage change. The implementation and BSD notice are
retained byte for byte; this is an attributed reuse, not an original digest.
"""
import hashlib

REPAIR_ID = "app-policy:display-digest"


def prepare_display_digest(source, report):
    path = 'ccast/axTLS/sha256.c'
    raw = (source / path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != report['upstream_source_sha256'][path]:
        raise ValueError('Display digest source differs from the pinned archive')
    text = raw.decode('latin1')
    includes = '#include <string.h>\n#include "os_port.h"\n#include "crypto.h"'
    if text.count(includes) != 1:
        raise ValueError('Unexpected pinned SHA-256 includes')
    declarations = '''#include <string.h>
#include <stdint.h>
typedef struct { uint32_t total[2], state[8]; uint8_t buffer[64]; } SHA256_CTX;
static void SHA256_Update(SHA256_CTX *, const uint8_t *, int);
'''
    text = text.replace(includes, declarations)
    for name in ('Init', 'Update', 'Final'):
        text = text.replace('\nvoid SHA256_' + name + '(', '\nstatic void SHA256_' + name + '(')
    generated = source / 'numlib/cm_display_sha256.h'
    generated.write_text(text, encoding='latin1')
    report['display_catalogue_digest'] = {
        'source': path, 'source_sha256': hashlib.sha256(raw).hexdigest(),
        'generated_sha256': hashlib.sha256(generated.read_bytes()).hexdigest(),
        'license': 'BSD-3-Clause', 'changes': 'includes and private linkage only',
    }
