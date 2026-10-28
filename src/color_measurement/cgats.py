"""CGATS text reader, faithful to Argyll 3.5.0's ``cgats/cgats.c``.

This is the *format* layer only: it turns CGATS text into tables of typed
values and knows nothing about what any particular table means. The semantic
layers sit on top of it -- see ``colorimeter_correction.py`` for ``.ccmx`` and
``.ccss``.

Scope and why it exists separately from ``spectral_exports.py``: that module
*writes* a bounded SPECT projection for Argyll's ``read_nxspect`` and states in
its own docstring that it is "not ... a general CGATS importer". Reading is the
opposite direction and has no implementation here, so this module supplies one.

Faithfulness rules taken from upstream, with citations:

* A file opens with an identifier token (``cgats.c:491`` matches it against the
  identifiers the caller registered with ``add_other``). ``.ccmx`` files open
  with ``CCMX`` and ``.ccss`` files with ``CCSS`` (``xicc/ccmx.c:330``,
  ``xicc/ccss.c`` respectively register exactly one, so the accepted table has
  ``oi == 0``).
* Field types are *inferred from the data*, not declared. ``cgats.c:679-697``
  scans every value in a column with ``guess_type`` (``cgats.c:1915-1961``) and keeps
  the most permissive type seen, in the order int < real < unquoted string <
  quoted string.
* A column whose inferred type is integer is promoted to real when the field
  name is a *standard* field of real type (``cgats.c:700-706``). ``XYZ_X``,
  ``XYZ_Y`` and ``XYZ_Z`` are such fields (``standard_field``), which is why a
  correction matrix written as ``1 0 0`` is still a real matrix upstream.
  ``SPEC_nnn`` is **not** a standard field, so its type comes only from data.
* A standard field whose inferred type disagrees with its declared type is an
  error (``cgats.c:708-712``).

Units: this module applies none. Values are returned exactly as written.
Limitations: it reads; it does not write. Table-level comments are discarded.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from .decimal_numbers import DECIMAL_RE as _REAL
from .decimal_numbers import decimal_number

__all__ = ["CgatsError", "CgatsTable", "read_cgats"]


class CgatsError(ValueError):
    """A CGATS file this reader refuses.

    NOT "one upstream would also refuse". This reader is deliberately stricter in
    several places, and claiming parity was false. The departures, each an
    upstream leniency that turns a malformed file into a silently wrong value
    rather than an error:

    * ``NUMBER_OF_FIELDS`` and ``NUMBER_OF_SETS`` are validated. Upstream never
      checks the first at all (it is a reserved keyword and is not stored) and
      guards the second with ``expsets != 0``, parsing both with ``atoi`` -- so a
      non-numeric or negative count reads as zero and is ignored.
    * A keyword's value must be on its own line. Upstream's ``R_KWORD_VALUE``
      state consumes the next token wherever it is, including the next line.
    * A keyword line carries exactly one value; a further token is refused.
      Upstream ignores further tokens on that line because a keyword must start
      a line (``cgats.c:437-440``); reading its value returns to the keyword
      state (``:613-616``).
    * The identifier must stand alone on line 1; upstream ignores trailing tokens.
    * A quoted value must close on its line; upstream's ``read_line`` keeps the
      line open to the closing quote or EOF.
    * Three "cope with a faulty table" recovery paths upstream has for a bare
      ``SAMPLE_ID`` opening the format section, ``BEGIN_DATA`` inside it, and a
      keyword inside it are not implemented.
    * A numeric cell must have a mantissa digit. Upstream types ``"e5"``, ``"."``
      and ``"1e"`` as numbers and ``atof`` yields 0.0.
    * ``.ccss`` grid keywords must be numeric and in range; upstream's ``atoi`` /
      ``atof`` accept anything and yield 0 or ``inf``.

    None of these appears in any correction file ArgyllCMS ships, and all 27 of
    those parse. The rule is that where upstream converts a malformed input into a
    quiet zero, this refuses instead: a correction built from a zero nobody wrote
    is worse than no correction.
    """


# Upstream's guess_type (cgats.c:1915-1961) accepts digits, a leading or
# post-exponent sign, at most one '.' before any 'e', and at most one 'e'. It
# does NOT accept a bare '.', 'inf' or 'nan'. This pattern is that grammar.
_INTEGER = re.compile(r"[-+]?[0-9]+\Z")
# A digit is required in the MANTISSA, which is one deliberate divergence from
# upstream. `guess_type` (cgats.c:1936-1953) sets its "is real" flag on seeing
# `e` and never checks that anything preceded it, so "e5", ".e5" and "+e5" type
# as r_t and `atof` yields 0.0: a corrupt matrix cell silently becomes zero.
# Refusing is stricter than Argyll on a file no correct writer produces, and the
# alternative is publishing a correction nobody computed.

INT = "int"
REAL = "real"
STRING = "string"
QUOTED = "quoted"

# Rank used by the column scan. cgats.c:684-695 breaks out early on a quoted
# string, promotes int->real->unquoted-string, and never demotes.
_RANK = {INT: 0, REAL: 1, STRING: 2, QUOTED: 3}

# standard_field() (``cgats.c:1735-1840``), for the fields this project reads.
#
# SCOPE, stated because the transcription rule differs: upstream matches by
# PREFIX and then inspects one character, so it also covers names this exact-name
# table cannot reach (XYZ_XX, RGB_Rx, CMYK_CC) and names not listed here at all
# (LAB_C, LAB_DE, XYY_*, STDEV_*). For `.ccmx` and `.ccss` that is complete:
# ccmx reads only XYZ_X/Y/Z, ccss only SPEC_nnn and SAMPLE_ID, and all four are
# right. A `.ti3` or `.cie` file read through `read_cgats` would infer the type
# of an unlisted column instead of taking the standard one -- which changes an
# all-integer column from real to int, and accepts a quoted one upstream refuses.
# SAMPLE_NAME is listed although standard_field has no case for it; upstream
# leaves it inferred.
_STANDARD_FIELDS: Mapping[str, str] = {
    "SAMPLE_ID": STRING,
    "SAMPLE_NAME": QUOTED,
    "STRING": QUOTED,
    "XYZ_X": REAL, "XYZ_Y": REAL, "XYZ_Z": REAL,
    "LAB_L": REAL, "LAB_A": REAL, "LAB_B": REAL,
    "RGB_R": REAL, "RGB_G": REAL, "RGB_B": REAL,
    "CMYK_C": REAL, "CMYK_M": REAL, "CMYK_Y": REAL, "CMYK_K": REAL,
    "CMY_C": REAL, "CMY_M": REAL, "CMY_Y": REAL,
    "D_RED": REAL, "D_GREEN": REAL, "D_BLUE": REAL, "D_VIS": REAL,
    "SPECTRAL_NM": REAL, "SPECTRAL_PCT": REAL,
}

# Section markers, recognised before any keyword interpretation.
_BEGIN_FORMAT = "BEGIN_DATA_FORMAT"
_END_FORMAT = "END_DATA_FORMAT"
_BEGIN_DATA = "BEGIN_DATA"
_END_DATA = "END_DATA"
_COUNT_FIELDS = "NUMBER_OF_FIELDS"
_COUNT_SETS = "NUMBER_OF_SETS"
_DECLARE = "KEYWORD"


@dataclass(frozen=True)
class CgatsTable:
    """One CGATS table: its keywords, its field names and its rows.

    ``field_types`` holds the inferred-and-promoted type per field, one of
    ``"int"``, ``"real"``, ``"string"`` or ``"quoted"``. ``rows`` holds the
    values already converted to ``int``/``float``/``str`` accordingly.
    ``raw_rows`` retains lexical cell tokens, including quotes. The ordered
    ``keyword_entries`` retains each name/raw-value occurrence, including
    field/set counts but excluding KEYWORD declarations. Semantic consumers
    can refuse conflicting declarations without changing generic last-value
    reading. ``declared_fields``/``declared_sets`` distinguish omitted counts.
    """

    identifier: str
    keywords: Mapping[str, str]
    fields: tuple[str, ...]
    field_types: Mapping[str, str]
    rows: tuple[tuple[object, ...], ...]
    raw_rows: tuple[tuple[str, ...], ...]
    keyword_entries: tuple[tuple[str, str], ...]
    declared_fields: int | None
    declared_sets: int | None

    def index(self, field: str) -> int:
        """Position of ``field``, or -1 when absent (upstream's find_field)."""
        try:
            return self.fields.index(field)
        except ValueError:
            return -1

    def reals(self, field: str) -> tuple[float, ...]:
        """Every value of a real-typed column, in row order.

        Raises ``CgatsError`` when the field is absent or is not real-typed,
        which is the check ``ccmx.c:293-296`` makes before reading a matrix.
        """
        position = self.index(field)
        if position < 0:
            raise CgatsError(f"File does not contain field {field}")
        if self.field_types[field] != REAL:
            raise CgatsError(f"File field {field} is wrong type")
        return tuple(float(row[position]) for row in self.rows)

    def numbers(self, field: str) -> tuple[float, ...]:
        """Every value of a numeric column, accepting an integer-typed one.

        ``SPEC_nnn`` is not a standard CGATS field, so an all-integer spectral
        column types as ``int``. ``read_ccss_cgats`` does not type-check it, but
        that does NOT mean upstream reads such a column correctly: an ``i_t``
        column is stored as a 4-byte ``int`` allocation, and the reader
        dereferences it as ``double *``. That is a wrong value and an overread,
        not a lenient read.

        Reading it as the number it is, is therefore a deliberate improvement on
        upstream rather than a match, and ``reals`` remains the strict accessor
        for fields whose type upstream does check.
        """
        position = self.index(field)
        if position < 0:
            raise CgatsError(f"File does not contain field {field}")
        if self.field_types[field] not in (REAL, INT):
            raise CgatsError(f"File field {field} is not numeric")
        try:
            return tuple(float(row[position]) for row in self.rows)
        except OverflowError as error:
            raise CgatsError(f"File field {field} exceeds binary64 range") from error


def _tokenize(line: str) -> list[str]:
    """Split one line into raw tokens, quote characters retained.

    Faithful to `get_token` (``pars.c:555-600``): a quote character toggles a
    quoted section, and while one is open a terminator does not end the token --
    which is how ``DESCRIPTOR "Xrite DTP94 & Dell 2408WFP"`` is one token. The
    quotes stay in the raw token; ``_unquote`` removes them later, and only when
    the token both begins and ends with one, exactly as ``unquote_cs`` does.

    Two consequences that a simpler "read to the closing quote" tokenizer gets
    wrong, both of which silently shortened a value rather than failing:

    * ``Dell"U2410"`` is ONE token. It does not start with a quote, so it is
      never unquoted, and the value keeps its inner quotes.
    * ``"a ""b"" c"`` is one token whose doubled quotes survive to ``_unquote``.

    A '#' inside a quoted section is content, not the start of a comment.
    """
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    for character in line:
        if character == '"':
            quoted = not quoted
            current.append(character)
            continue
        if not quoted:
            if character == "#":
                break
            if character in " \t\r\n":
                if current:
                    tokens.append("".join(current))
                    current = []
                continue
        current.append(character)
    if quoted:
        raise CgatsError("Unterminated quoted string")
    if current:
        tokens.append("".join(current))
    return tokens


def _unquote(raw: str) -> str:
    """`unquote_cs` (``cgats.c:1896-1911``): strip outer quotes, collapse "" -> \"."""
    if len(raw) < 2 or raw[0] != '"' or raw[-1] != '"':
        return raw
    out: list[str] = []
    skipped = True
    for index in range(1, len(raw) - 1):
        if skipped or raw[index - 1] != '"' or raw[index] != '"':
            out.append(raw[index])
            skipped = False
        else:
            skipped = True
    return "".join(out)


def _guess_type(raw: str) -> str:
    """Upstream's ``guess_type`` (``cgats.c:1915-1961``) over one RAW token.

    The quoted tests come first and are exactly upstream's: a token starting with
    a quote is ``cs_t`` when it also ends with one and ``nqcs_t`` otherwise. A
    token that merely CONTAINS a quote never reaches them.
    """
    if raw.startswith('"'):
        return QUOTED if len(raw) >= 2 and raw.endswith('"') else STRING
    if _INTEGER.fullmatch(raw):
        return INT
    if _REAL.fullmatch(raw):
        return REAL
    return STRING


def _column_type(field: str, values: Sequence[str]) -> str:
    """Infer one column's type, then apply upstream's promotion and check.

    Mirrors cgats.c:679-712: scan for the most permissive type, promote an
    ambiguous integer column to the standard field's type, and refuse a column
    whose settled type contradicts a standard field.
    """
    inferred = INT
    for raw in values:
        guessed = _guess_type(raw)
        if _RANK[guessed] > _RANK[inferred]:
            inferred = guessed
        if inferred == QUOTED:
            break
    standard = _STANDARD_FIELDS.get(field)
    if standard is None:
        return inferred
    promotable = (
        (standard == REAL and inferred == INT)
        or (standard in (STRING, QUOTED) and inferred in (INT, REAL))
        or (standard == STRING and inferred == QUOTED)
        or (standard == QUOTED and inferred == STRING)
    )
    if promotable:
        return standard
    if standard != inferred:
        raise CgatsError(
            f"Field '{field}' has unexpected type, should be '{standard}', is '{inferred}'"
        )
    return inferred


def _real_number(raw: str) -> float:
    """Keep representable subnormals; refuse overflow and fabricated zero.

    The ASCII grammar comes first: ``float`` and ``Decimal`` also accept digit-group
    underscores and every Unicode decimal digit, which upstream's ``atof`` reads as
    a different number (``"4_00"`` stops at the underscore and is 4; Arabic-Indic
    digits are nothing and 0). A keyword value reaches here unscanned, so the
    gate the cell scanner already applies is applied again here.
    """
    try:
        return decimal_number(raw)
    except ValueError as error:
        raise CgatsError(str(error)) from None


def _convert(raw: str, kind: str) -> object:
    if kind == INT:
        try:
            return int(raw)
        except ValueError as error:
            raise CgatsError(f"Invalid integer number: {raw!r}") from error
    if kind == REAL:
        return _real_number(raw)
    return _unquote(raw)


class _TableBuilder:
    """Accumulates one table's keywords, fields and raw rows."""

    def __init__(self) -> None:
        self.keywords: dict[str, str] = {}
        self.keyword_entries: list[tuple[str, str]] = []
        self.declared: set[str] = set()
        self.fields: list[str] = []
        self.raw: list[list[str]] = []
        self.declared_fields: int | None = None
        self.declared_sets: int | None = None

    def is_empty(self) -> bool:
        return not (self.keywords or self.fields or self.raw)

    def build(self, identifier: str) -> CgatsTable:
        if self.declared_fields is not None and self.declared_fields != len(self.fields):
            raise CgatsError(
                f"{_COUNT_FIELDS} is {self.declared_fields} but "
                f"{len(self.fields)} fields were declared"
            )
        if self.declared_sets is not None and self.declared_sets != len(self.raw):
            raise CgatsError(
                f"{_COUNT_SETS} is {self.declared_sets} but {len(self.raw)} sets were read"
            )
        types: dict[str, str] = {}
        for position, field in enumerate(self.fields):
            column = [row[position] for row in self.raw]
            types[field] = _column_type(field, column)
        rows = tuple(
            tuple(
                _convert(raw, types[self.fields[position]])
                for position, raw in enumerate(row)
            )
            for row in self.raw
        )
        return CgatsTable(
            identifier=identifier,
            keywords=dict(self.keywords),
            fields=tuple(self.fields),
            field_types=types,
            rows=rows,
            raw_rows=tuple(tuple(row) for row in self.raw),
            keyword_entries=tuple(self.keyword_entries),
            declared_fields=self.declared_fields,
            declared_sets=self.declared_sets,
        )


def _lines(text: str) -> Iterator[list[str]]:
    for line in text.splitlines():
        tokens = _tokenize(line)
        if tokens:
            yield tokens


def read_cgats(text: str, *, expected_identifier: str | None = None) -> tuple[CgatsTable, ...]:
    """Parse CGATS text into its tables.

    ``expected_identifier`` is the single identifier a caller registered with
    ``add_other`` upstream; supplying it makes a file of another kind an error
    the way ``cgats.c:491`` does for an unregistered identifier.

    Raises ``CgatsError`` for anything upstream's reader refuses and for the
    malformed values it would quietly zero; see ``CgatsError``.
    """
    stream = _lines(text)
    try:
        first = next(stream)
    except StopIteration:
        raise CgatsError("Empty file, no CGATS identifier") from None
    identifier = first[0]
    if len(first) != 1:
        raise CgatsError("The CGATS identifier must stand alone on the first line")
    if expected_identifier is not None and identifier != expected_identifier:
        raise CgatsError(
            f"Input file isn't a {expected_identifier} format file (identifier {identifier!r})"
        )

    tables: list[CgatsTable] = []
    table = _TableBuilder()
    # None -> keywords; "format" -> inside BEGIN_DATA_FORMAT; "data" -> inside
    # BEGIN_DATA. Rows may wrap lines, so data tokens accumulate in `pending`.
    section: str | None = None
    pending: list[str] = []

    # Upstream begins a new table on ANY token once the previous table's data has
    # finished, not only on a repeated identifier: `cgats.c:762` sets `tablef =
    # p->ntables` immediately after END_DATA, and `cgats.c:511` opens a table when
    # `p->ntables == tablef`. Trailing content therefore makes a SECOND table,
    # which `ccmx.c:203` and `ccss.c:272` refuse as "not exactly one table".
    #
    # Merging it into the first table instead let anything appended after END_DATA
    # override INSTRUMENT, TECHNOLOGY and DISPLAY_TYPE_BASE_ID -- and cbid and
    # dtech are handed to the instrument, so it changed what reached hardware.
    finished = False
    for tokens in stream:
        head = tokens[0]
        if section is None:
            if finished:
                tables.append(table.build(identifier))
                table = _TableBuilder()
                finished = False
                if head == identifier:
                    continue
            if head == _BEGIN_FORMAT:
                if len(tokens) != 1:
                    raise CgatsError(f"{_BEGIN_FORMAT} takes no arguments")
                if table.fields:
                    raise CgatsError("A table declares its data format once")
                section = "format"
                continue
            if head == _BEGIN_DATA:
                if len(tokens) != 1:
                    raise CgatsError(f"{_BEGIN_DATA} takes no arguments")
                if not table.fields:
                    raise CgatsError(f"{_BEGIN_DATA} before any data format")
                section, pending = "data", []
                continue
            if len(tokens) < 2:
                raise CgatsError(f"Keyword {head!r} has no value")
            if len(tokens) != 2:
                raise CgatsError(f"Keyword {head!r} must have exactly one value")
            value = _unquote(tokens[1])
            if head == _DECLARE:
                table.declared.add(value)
                continue
            table.keyword_entries.append((head, tokens[1]))
            if head == _COUNT_FIELDS:
                table.declared_fields = _positive(value, _COUNT_FIELDS)
                continue
            if head == _COUNT_SETS:
                table.declared_sets = _positive(value, _COUNT_SETS)
                continue
            # Upstream stores any further identifier/value line as a keyword;
            # an undeclared one is accepted on read (only writing emits the
            # KEYWORD declarations, cgats.c:1505).
            table.keywords[head] = value
            continue

        if section == "format":
            if head == _END_FORMAT:
                section = None
                continue
            table.fields.extend(_unquote(raw) for raw in tokens)
            continue

        if head == _END_DATA:
            if pending:
                raise CgatsError(
                    f"{_END_DATA} with {len(pending)} values left over from a partial set"
                )
            section = None
            finished = True
            continue
        pending.extend(tokens)
        width = len(table.fields)
        while len(pending) >= width:
            table.raw.append(pending[:width])
            del pending[:width]

    if section is not None:
        raise CgatsError(f"File ended inside a {section} section")
    if not table.is_empty():
        tables.append(table.build(identifier))
    if not tables:
        raise CgatsError("File contains no tables")
    return tuple(tables)


def _positive(value: str, keyword: str) -> int:
    # The same ASCII grammar as a cell: `int` alone takes "0_4" and "٣", which
    # upstream's atoi reads as 0 (cgats.c:594).
    if _INTEGER.match(value) is None:
        raise CgatsError(f"{keyword} is not an integer: {value!r}")
    try:
        number = int(value)
    except ValueError:
        raise CgatsError(f"{keyword} is not an integer: {value!r}") from None
    if number < 0:
        raise CgatsError(f"{keyword} is negative: {number}")
    return number
