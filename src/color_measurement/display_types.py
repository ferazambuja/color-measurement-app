"""Named Argyll display calibrations, bound to an observed native catalogue.

The driver owns names and loaded calibration data. Native SHA-256 identities
cover the index, metadata, and loaded CCMX/CCSS coefficients; selector aliases
are deliberately absent. Current technology/base/refresh readback does not
identify a selected index (inst.h get_disptechi has no such return value).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass

LIMIT = 1000
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ROW_KEYS = {"index", "id", "label", "technology_code", "refresh_mode",
             "base_calibration_id", "kind", "source_path", "custom", "identity_scope",
             "available", "unavailable_reason", "native_default",
             "native_label_bytes", "native_source_path_bytes"}


def _integer(value, low=0, high=0xffffffff):
    return type(value) is int and low <= value <= high


def _digest(value):
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _native_text(value, encoded, maximum, *, required=False):
    """Accept exact UTF-8 text or lossless invalid bytes, never both or a guessed decode."""
    if encoded is None:
        if value is None and not required:
            return False
        if (not isinstance(value, str) or not value or '\0' in value
                or len(value.encode('utf-8')) > maximum):
            raise ValueError('Invalid native display-type text')
        return False
    if (value is not None or not isinstance(encoded, str) or not encoded
            or len(encoded) > 2 * maximum or len(encoded) % 2
            or re.fullmatch('[0-9a-f]+', encoded) is None):
        raise ValueError('Invalid native display-type bytes')
    raw = bytes.fromhex(encoded)
    if b'\0' in raw:
        raise ValueError('Native display-type bytes contain a terminator')
    try:
        raw.decode('utf-8')
    except UnicodeDecodeError:
        return True
    raise ValueError('Valid native UTF-8 must be published as text')


def validate_row(row, index):
    """Validate one bounded row, preserving exact IDs and duplicate labels."""
    if (not isinstance(row, dict) or set(row) != _ROW_KEYS
            or type(row['index']) is not int or row['index'] != index or index >= LIMIT
            or not _digest(row['id'])
            or type(row['available']) is not bool
            or type(row['native_default']) is not bool
            or not _integer(row['technology_code'])
            or not _integer(row['refresh_mode'], -1, 1)
            or not _integer(row['base_calibration_id'], 0, 0x7fffffff)
            or row['kind'] not in {'builtin', 'instrument_matrix', 'ccmx', 'ccss'}
            or row['identity_scope'] != ('loaded_correction_data'
                                        if row['kind'] in {'ccmx', 'ccss'}
                                        else 'catalogue_metadata')
            or type(row['custom']) is not bool):
        raise ValueError('Invalid native display-type entry')
    bad_label = _native_text(row['label'], row['native_label_bytes'], 600, required=True)
    bad_path = _native_text(row['source_path'], row['native_source_path_bytes'], 1024)
    reason = ('Native label and source path are not valid UTF-8' if bad_label and bad_path else
              'Native label is not valid UTF-8' if bad_label else
              'Native source path is not valid UTF-8' if bad_path else None)
    if row['available'] != (reason is None) or row['unavailable_reason'] != reason:
        raise ValueError('Contradictory native display-type availability')
    return copy.deepcopy(row)


def catalogue(summary, rows):
    """Join native bounded row frames with their terminal catalogue summary."""
    keys = {'status', 'native_code', 'count', 'catalogue_id', 'applied_choice_id', 'readback'}
    if (not isinstance(summary, dict) or set(summary) != keys
            or summary['status'] not in {'available', 'unsupported', 'failed'}
            or not _integer(summary['native_code'])
            or not _integer(summary['count'], 0, LIMIT)
            or not isinstance(rows, list) or summary['count'] != len(rows)):
        raise ValueError('Invalid native display-type catalogue')
    available = summary['status'] == 'available'
    if (available and (summary['native_code'] != 0 or not _digest(summary['catalogue_id']))
            or not available and (rows or summary['catalogue_id'] is not None
                                  or summary['native_code'] == 0)):
        raise ValueError('Contradictory native display-type catalogue')
    entries = [validate_row(row, index) for index, row in enumerate(rows)]
    ids = [row['id'] for row in entries]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate native display-type identity')
    if summary['applied_choice_id'] is not None and summary['applied_choice_id'] not in {
            row['id'] for row in entries if row['available']}:
        raise ValueError('Applied display type is absent or unavailable in the native catalogue')
    readback = summary['readback']
    if (not isinstance(readback, dict)
            or set(readback) != {'native_code', 'technology_code', 'refresh_mode',
                                'base_calibration_id'}
            or not _integer(readback['native_code'])
            or any(readback[key] is not None and not _integer(readback[key], *bounds)
                   for key, bounds in {'technology_code': (0, 0xffffffff),
                                       'refresh_mode': (-1, 1),
                                       'base_calibration_id': (0, 0x7fffffff)}.items())
            or readback['native_code'] != 0 and any(
                readback[key] is not None for key in readback if key != 'native_code')):
        raise ValueError('Invalid native display-type readback')
    return copy.deepcopy(summary) | {'entries': entries}


def validate_catalogue(value):
    if not isinstance(value, dict) or 'entries' not in value:
        raise ValueError('An observed display-type catalogue is required')
    return catalogue({key: item for key, item in value.items() if key != 'entries'},
                     value['entries'])


@dataclass(frozen=True)
class DisplayTypeSelection:
    """A named choice resolved from one observed catalogue, never a bare index."""
    index: int
    catalogue_id: str
    choice_id: str

    def __post_init__(self):
        if not _integer(self.index, 0, LIMIT - 1) or not all(
                _digest(value) for value in (self.catalogue_id, self.choice_id)):
            raise ValueError('A display selection requires catalogue and choice identities')

    @classmethod
    def from_catalogue(cls, value, choice_id):
        if not _digest(choice_id):
            raise ValueError("display_type is a named choice identity, or null")
        value = validate_catalogue(value)
        if value['status'] != 'available':
            raise ValueError('Display-type selection is unavailable')
        row = next((row for row in value['entries'] if row['id'] == choice_id), None)
        if row is None:
            raise ValueError('Display type is not offered in the observed catalogue')
        if not row['available']:
            raise ValueError(f"Display-type selection is unavailable: {row['unavailable_reason']}")
        return cls(row['index'], value['catalogue_id'], row['id'])
