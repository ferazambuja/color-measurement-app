"""The CGATS reader, against Argyll 3.5.0's own rules in ``cgats/cgats.c``."""

import sys

import pytest

from color_measurement.cgats import INT, QUOTED, REAL, STRING, CgatsError, read_cgats


def table(identifier="CCMX", keywords="", fields="XYZ_X XYZ_Y XYZ_Z", rows=("1.0 2.0 3.0",)):
    body = "\n".join(rows)
    return (
        f"{identifier}\n\n{keywords}\n"
        f"NUMBER_OF_FIELDS {len(fields.split())}\n"
        f"BEGIN_DATA_FORMAT\n{fields}\nEND_DATA_FORMAT\n\n"
        f"NUMBER_OF_SETS {len(rows)}\nBEGIN_DATA\n{body}\nEND_DATA\n"
    )


def test_a_minimal_table_reads_its_identifier_fields_and_rows():
    (parsed,) = read_cgats(table())
    assert parsed.identifier == "CCMX"
    assert parsed.fields == ("XYZ_X", "XYZ_Y", "XYZ_Z")
    assert parsed.rows == ((1.0, 2.0, 3.0),)


@pytest.mark.parametrize("value", ["1e-999", "-1e-999", "1e999", "-1e999"])
def test_real_cells_refuse_overflow_and_nonzero_underflow(value):
    with pytest.raises(CgatsError, match="not finite or underflows"):
        read_cgats(table(rows=(f"{value} 0.0 1.0",)))


def test_integer_numeric_column_overflow_uses_the_cgats_error_channel():
    (parsed,) = read_cgats(table(fields="SPEC_400", rows=("9" * 310,)))
    assert parsed.field_types["SPEC_400"] == INT
    with pytest.raises(CgatsError, match="exceeds binary64 range") as caught:
        parsed.numbers("SPEC_400")
    assert isinstance(caught.value.__cause__, OverflowError)


def test_integer_digit_limit_uses_the_cgats_error_channel():
    limit = sys.get_int_max_str_digits()
    if not limit:
        pytest.skip("Interpreter integer digit limit is disabled")
    with pytest.raises(CgatsError, match="Invalid integer number") as caught:
        read_cgats(table(fields="SPEC_400", rows=("9" * (limit + 1),)))
    assert type(caught.value.__cause__) is ValueError


@pytest.mark.parametrize("value", ["1e-320", "-1e-320", "5e-324", "0e-999", "-0e-999"])
def test_real_cells_preserve_subnormals_and_literal_zero(value):
    (parsed,) = read_cgats(table(rows=(f"{value} 0.0 1.0",)))
    assert parsed.rows[0][0].hex() == float(value).hex()


def test_the_expected_identifier_refuses_a_file_of_another_kind():
    with pytest.raises(CgatsError, match="isn't a CCSS format file"):
        read_cgats(table(identifier="CCMX"), expected_identifier="CCSS")


def test_an_empty_file_is_refused_rather_than_read_as_an_empty_table():
    with pytest.raises(CgatsError, match="Empty file"):
        read_cgats("")


def test_keywords_carry_quoted_values_containing_spaces():
    text = table(keywords='DESCRIPTOR "Xrite DTP94 & Dell 2408WFP"\nCOLOR_REP "XYZ"\n')
    (parsed,) = read_cgats(text)
    assert parsed.keywords["DESCRIPTOR"] == "Xrite DTP94 & Dell 2408WFP"
    assert parsed.keywords["COLOR_REP"] == "XYZ"


def test_a_repeated_keyword_in_one_table_keeps_the_last_value():
    """cgats.c:601-603 replaces an existing keyword on a repeat; this reader does the
    same (plain assignment), so a later DESCRIPTOR wins rather than being refused."""
    text = table(keywords='DESCRIPTOR "first"\nDESCRIPTOR "second"\n')
    (parsed,) = read_cgats(text)
    assert parsed.keywords["DESCRIPTOR"] == "second"
    assert parsed.keyword_entries[:2] == (("DESCRIPTOR", '"first"'),
                                          ("DESCRIPTOR", '"second"'))


def test_a_keyword_declaration_is_accepted_and_not_stored_as_a_keyword():
    """cgats.c:886 emits KEYWORD only when writing; a reader just accepts it."""
    text = table(keywords='KEYWORD "INSTRUMENT"\nINSTRUMENT "Xrite DTP94"\n')
    (parsed,) = read_cgats(text)
    assert parsed.keywords["INSTRUMENT"] == "Xrite DTP94"
    assert "KEYWORD" not in parsed.keywords


def test_comments_run_to_end_of_line_and_do_not_end_a_section():
    text = table(rows=("1.0 2.0 3.0  # trailing note",)) .replace(
        "BEGIN_DATA\n", "# a whole-line comment\nBEGIN_DATA\n"
    )
    (parsed,) = read_cgats(text)
    assert parsed.rows == ((1.0, 2.0, 3.0),)


def test_a_row_may_wrap_across_lines():
    """cgats.c reads data as tokens, not as lines: one set may span two lines."""
    text = ("CCMX\nNUMBER_OF_FIELDS 3\nBEGIN_DATA_FORMAT\nXYZ_X XYZ_Y XYZ_Z\n"
            "END_DATA_FORMAT\nNUMBER_OF_SETS 1\nBEGIN_DATA\n1.0 2.0\n3.0\nEND_DATA\n")
    (parsed,) = read_cgats(text)
    assert parsed.rows == ((1.0, 2.0, 3.0),)


@pytest.mark.parametrize("value,kind,expected", [
    ("7", INT, 7),
    ("7.5", REAL, 7.5),
    ("-1e-3", REAL, -1e-3),
    ("+2.", REAL, 2.0),
    ("red", STRING, "red"),
])
def test_a_non_standard_field_takes_its_type_from_the_data(value, kind, expected):
    text = table(fields="SPEC_400", rows=(value,))
    (parsed,) = read_cgats(text)
    assert parsed.field_types["SPEC_400"] == kind
    assert parsed.rows == ((expected,),)


def test_a_quoted_value_types_the_whole_column_as_quoted():
    text = table(fields="SPEC_400", rows=("1.5", '"a label"'))
    (parsed,) = read_cgats(text)
    assert parsed.field_types["SPEC_400"] == QUOTED


def test_one_real_value_promotes_the_whole_column_to_real():
    """cgats.c:691-693 keeps the most permissive type seen in the column."""
    text = table(fields="SPEC_400", rows=("1", "2.5", "3"))
    (parsed,) = read_cgats(text)
    assert parsed.field_types["SPEC_400"] == REAL
    assert parsed.rows == ((1.0,), (2.5,), (3.0,))


def test_an_all_integer_standard_real_field_is_promoted_to_real():
    """cgats.c:700-706. XYZ_X/Y/Z are standard real fields, so an identity matrix
    written as bare integers is still a real matrix -- and ccmx.c:293-296 would
    otherwise reject it for being the wrong type."""
    text = table(rows=("1 0 0", "0 1 0", "0 0 1"))
    (parsed,) = read_cgats(text)
    assert parsed.field_types["XYZ_X"] == REAL
    assert parsed.reals("XYZ_X") == (1.0, 0.0, 0.0)


def test_a_non_standard_all_integer_field_is_not_promoted():
    """The counterpart: SPEC_nnn is not a standard field, so nothing promotes it."""
    (parsed,) = read_cgats(table(fields="SPEC_400", rows=("1", "0")))
    assert parsed.field_types["SPEC_400"] == INT
    with pytest.raises(CgatsError, match="wrong type"):
        parsed.reals("SPEC_400")
    assert parsed.numbers("SPEC_400") == (1.0, 0.0)


def test_a_standard_field_holding_the_wrong_type_is_refused():
    """cgats.c:708-712 errors rather than accepting the contradiction."""
    with pytest.raises(CgatsError, match="unexpected type"):
        read_cgats(table(rows=("red 2.0 3.0",)))


def test_a_missing_field_is_reported_by_name():
    (parsed,) = read_cgats(table())
    assert parsed.index("LAB_L") == -1
    with pytest.raises(CgatsError, match="does not contain field LAB_L"):
        parsed.reals("LAB_L")


def test_a_declared_field_count_that_disagrees_with_the_format_is_refused():
    text = table().replace("NUMBER_OF_FIELDS 3", "NUMBER_OF_FIELDS 4")
    with pytest.raises(CgatsError, match="NUMBER_OF_FIELDS is 4"):
        read_cgats(text)


def test_a_declared_set_count_that_disagrees_with_the_data_is_refused():
    text = table(rows=("1.0 2.0 3.0", "4.0 5.0 6.0")).replace("NUMBER_OF_SETS 2",
                                                              "NUMBER_OF_SETS 3")
    with pytest.raises(CgatsError, match="NUMBER_OF_SETS is 3"):
        read_cgats(text)


def test_a_partial_final_set_is_refused_rather_than_padded():
    text = table(rows=("1.0 2.0 3.0", "4.0 5.0")).replace("NUMBER_OF_SETS 2",
                                                          "NUMBER_OF_SETS 1")
    with pytest.raises(CgatsError, match="left over from a partial set"):
        read_cgats(text)


def test_an_unterminated_quote_is_refused_rather_than_truncated():
    with pytest.raises(CgatsError, match="Unterminated quoted string"):
        read_cgats(table(keywords='DESCRIPTOR "no closing quote\n'))


def test_data_before_any_format_is_refused():
    text = "CCMX\nNUMBER_OF_SETS 1\nBEGIN_DATA\n1.0\nEND_DATA\n"
    with pytest.raises(CgatsError, match="BEGIN_DATA before any data format"):
        read_cgats(text)


def test_a_file_ending_inside_a_section_is_refused():
    with pytest.raises(CgatsError, match="ended inside a data section"):
        read_cgats(table().replace("END_DATA\n", ""))


def test_a_repeated_identifier_starts_a_second_table():
    (first, second) = read_cgats(table() + table(rows=("9.0 8.0 7.0",)))
    assert first.rows == ((1.0, 2.0, 3.0),)
    assert second.rows == ((9.0, 8.0, 7.0),)


# -- faithfulness of the tokenizer, from pars.c get_token and cgats.c unquote_cs

def test_a_quoted_value_keeps_its_spaces():
    """`get_token` (pars.c:560-566) suspends terminators inside a quoted section."""
    (parsed,) = read_cgats(table(keywords='DESCRIPTOR "Xrite DTP94 & Dell 2408WFP"\n'))
    assert parsed.keywords["DESCRIPTOR"] == "Xrite DTP94 & Dell 2408WFP"


def test_a_doubled_quote_inside_a_quoted_value_collapses_to_one():
    """`unquote_cs` (cgats.c:1896-1911) skips the second of two quotes in a row.

    Reading to the first closing quote instead returned 'a ' -- a silently shorter
    string, with no error.
    """
    (parsed,) = read_cgats(table(keywords='DESCRIPTOR "a ""b"" c"\n'))
    assert parsed.keywords["DESCRIPTOR"] == 'a "b" c'


def test_a_token_that_merely_contains_a_quote_is_not_unquoted_or_split():
    """`unquote_cs` returns untouched anything not both starting and ending with a
    quote, and `get_token` ends a token only at an unquoted terminator. Splitting
    at the embedded quote instead returned 'Dell'."""
    (parsed,) = read_cgats(table(keywords='DISPLAY Dell"U2410"\n'))
    assert parsed.keywords["DISPLAY"] == 'Dell"U2410"'


def test_a_hash_inside_a_quoted_value_is_content_not_a_comment():
    (parsed,) = read_cgats(table(keywords='DESCRIPTOR "sample #3"\n'))
    assert parsed.keywords["DESCRIPTOR"] == "sample #3"


def test_content_after_end_data_starts_a_second_table():
    """cgats.c:762 sets `tablef` right after END_DATA and :511 opens a table when
    `ntables == tablef` -- for ANY token, not only a repeated identifier.

    Merging it into the first table let anything appended override INSTRUMENT,
    TECHNOLOGY and DISPLAY_TYPE_BASE_ID. Two of those reach the instrument.
    """
    tables = read_cgats(table() + 'INSTRUMENT "ATTACKER"\nDISPLAY_TYPE_BASE_ID 7\n')
    assert len(tables) == 2
    assert "INSTRUMENT" not in tables[0].keywords
    assert tables[1].keywords == {"INSTRUMENT": "ATTACKER", "DISPLAY_TYPE_BASE_ID": "7"}


def test_trailing_whitespace_and_comments_do_not_start_a_table():
    (parsed,) = read_cgats(table() + "\n\n   # just a comment\n\n")
    assert parsed.rows == ((1.0, 2.0, 3.0),)


@pytest.mark.parametrize("token", ["e5", "E5", ".e5", "+e5", "-e5", ".E-5", ".", "+", "-",
                                   "1e", "1e+", "1e-"])
def test_a_number_with_no_mantissa_digit_is_not_a_real(token):
    """Deliberate divergence, and the reason is that upstream's is unsafe.

    `guess_type` (cgats.c:1936-1953) sets its real flag on seeing 'e' without
    checking that a digit preceded it, so "e5" types as r_t and `atof` yields
    0.0 -- a corrupt matrix cell silently becomes zero. Here it types as a string,
    so a standard real field refuses it through the documented error channel
    instead of reaching float() and raising a bare ValueError. The same holds
    for a dangling exponent: upstream types "1e" as a real and `atof` yields 1.0.
    """
    (parsed,) = read_cgats(table(fields="SPEC_400", rows=(token,)))
    assert parsed.field_types["SPEC_400"] == STRING
    with pytest.raises(CgatsError, match="wrong type"):
        parsed.reals("SPEC_400")


# -- error paths that were correct but unpinned ------------------------------

def test_the_identifier_must_stand_alone_on_the_first_line():
    with pytest.raises(CgatsError, match="identifier must stand alone"):
        read_cgats(table().replace("CCMX\n", "CCMX extra\n", 1))


@pytest.mark.parametrize("marker", ["BEGIN_DATA_FORMAT", "BEGIN_DATA"])
def test_a_section_marker_takes_no_arguments(marker):
    with pytest.raises(CgatsError, match=f"{marker} takes no arguments"):
        read_cgats(table().replace(f"{marker}\n", f"{marker} junk\n", 1))


def test_a_table_declares_its_data_format_once():
    doubled = table().replace(
        "BEGIN_DATA_FORMAT\nXYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\n",
        "BEGIN_DATA_FORMAT\nXYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\n"
        "BEGIN_DATA_FORMAT\nXYZ_X\nEND_DATA_FORMAT\n", 1)
    with pytest.raises(CgatsError, match="declares its data format once"):
        read_cgats(doubled)


def test_a_keyword_with_no_value_is_refused():
    with pytest.raises(CgatsError, match="has no value"):
        read_cgats(table(keywords="DESCRIPTOR\n"))


@pytest.mark.parametrize("keyword", ["NUMBER_OF_FIELDS", "NUMBER_OF_SETS"])
@pytest.mark.parametrize("value", ["xyz", "-3", "0_3", "٣", "3.0"])
def test_a_count_that_is_not_a_non_negative_integer_is_refused(keyword, value):
    """Both _positive failure arms survived: a non-integer returning 0 and a
    negative one passing. Upstream reads these with atoi and ignores the result,
    so a `NUMBER_OF_FIELDS -3` silently became zero.

    `int` alone is the wrong gate: it reads "0_3" and an Arabic-Indic "٣" as 3,
    where atoi reads 0. The count must match the
    cell grammar's ASCII digits, exactly."""
    original = "NUMBER_OF_FIELDS 3" if keyword == "NUMBER_OF_FIELDS" else "NUMBER_OF_SETS 1"
    with pytest.raises(CgatsError, match=f"{keyword} is (not an integer|negative)"):
        read_cgats(table().replace(original, f"{keyword} {value}", 1))


def test_numbers_refuses_a_column_that_is_not_numeric():
    """Widening its accepted set to include the string types survived."""
    (parsed,) = read_cgats(table(fields="SPEC_400", rows=("red", "green", "blue")))
    with pytest.raises(CgatsError, match="is not numeric"):
        parsed.numbers("SPEC_400")
    with pytest.raises(CgatsError, match="does not contain field"):
        parsed.numbers("SPEC_999")


def test_an_integer_column_yields_integers_and_a_real_column_yields_floats():
    """field_types was asserted; the Python type of the value was not."""
    (parsed,) = read_cgats(table(fields="SPEC_400", rows=("1", "2")))
    assert [type(row[0]) for row in parsed.rows] == [int, int]
    (parsed,) = read_cgats(table(fields="SPEC_400", rows=("1.5", "2")))
    assert [type(row[0]) for row in parsed.rows] == [float, float]


def test_a_signed_integer_is_still_an_integer():
    """Dropping the sign from _INTEGER survived, retyping a signed column as real."""
    (parsed,) = read_cgats(table(fields="SPEC_400", rows=("-1", "+2")))
    assert parsed.field_types["SPEC_400"] == INT
    assert parsed.rows == ((-1,), (2,))


def test_a_lone_quote_is_an_unterminated_string_not_a_quoted_value():
    """guess_type requires length >= 2 before calling a token quoted; dropping that
    made a single `"` type as QUOTED."""
    with pytest.raises(CgatsError, match="Unterminated quoted string"):
        read_cgats(table(fields="SPEC_400", rows=('"',)))


def test_a_standard_string_field_is_promoted_from_either_string_form():
    """Both string<->quoted promotion arms were independently droppable, and no
    test used a field other than XYZ_* or SPEC_*."""
    # SAMPLE_ID is standard nqcs_t: a quoted column promotes down to it.
    (parsed,) = read_cgats(table(fields="SAMPLE_ID", rows=('"a"', '"b"')))
    assert parsed.field_types["SAMPLE_ID"] == STRING
    # STRING is standard cs_t: an unquoted column promotes up to it.
    (parsed,) = read_cgats(table(fields="STRING", rows=("a", "b")))
    assert parsed.field_types["STRING"] == QUOTED


def test_a_file_whose_only_table_is_empty_is_refused():
    with pytest.raises(CgatsError, match="contains no tables"):
        read_cgats("CCMX\n")


@pytest.mark.parametrize("keyword", [
    "DISPLAY_TYPE_BASE_ID 3 4", 'DESCRIPTOR "one value" extra',
    'KEYWORD "INSTRUMENT" extra', "NUMBER_OF_FIELDS 3 4", "NUMBER_OF_SETS 1 2",
])
def test_keyword_values_cannot_silently_discard_extra_tokens(keyword):
    with pytest.raises(CgatsError, match="exactly one value"):
        read_cgats(table(keywords=keyword + "\n"))


def test_keyword_token_bound_preserves_quoted_spaces_hashes_and_comments():
    (parsed,) = read_cgats(table(keywords=
        'DESCRIPTOR "display #3 with spaces" # ignored comment\n'
        "DISPLAY_TYPE_BASE_ID 3 # ignored comment\n"))
    assert parsed.keywords["DESCRIPTOR"] == "display #3 with spaces"
    assert parsed.keywords["DISPLAY_TYPE_BASE_ID"] == "3"


@pytest.mark.parametrize("digits", [310, 4301])
def test_a_giant_integer_in_a_real_column_is_refused_as_not_finite(digits):
    """float() has no digit limit and overflows to inf, so the REAL path must say
    so rather than let OverflowError or Python's own ValueError escape."""
    with pytest.raises(CgatsError, match="not finite or underflows"):
        read_cgats(table(rows=("1.5 0.0 1.0", "9" * digits + " 0.0 1.0")))
