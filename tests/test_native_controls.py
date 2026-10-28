"""Shared native control and original-byte contract checks; no instrument I/O."""

import json
import math
import re
import time
from copy import deepcopy
from pathlib import Path

import pytest

from color_measurement import display_types, instrument, native_controls, native_protocol
from color_measurement.argyll_description import validate_native_identity
from color_measurement.instrument import (
    Instrument,
    InstrumentError,
    native_control_facts,
    native_facts,
)
from color_measurement.native_controls import (
    ACK_ONLY_FIELDS,
    CONTROL_FIELDS,
    NativeControls,
    NativeControlState,
    ack_only_mask,
)
from color_measurement.native_protocol import (
    NATIVE_RAW_CHUNK_BYTES,
    NATIVE_RAW_MAX_BYTES,
    NATIVE_RAW_MAX_EXCHANGES,
    NATIVE_RAW_MAX_TOTAL_BYTES,
    ProtocolError,
)

_NATIVE = "tools/native_argyll/"
_SPECTRO = _NATIVE + "contributions/cr_pr/files/spectro/"
_PRODUCER_BOUNDS = (
    (_NATIVE + "cr250_control.h", "CM_CR250_CONTROL_REPLIES",
     native_protocol.NATIVE_CR_CONTROL_MAX_REPLIES),
    (_NATIVE + "display_types.h", "CM_DISPLAY_LIMIT", display_types.LIMIT),
    (_NATIVE + "session.c", "CM_MAX_PATCHES", instrument.MAX_PATCHES),
    (_NATIVE + "session.h", "CM_MAX_SHEETS", instrument.MAX_SHEETS),
    (_SPECTRO + "cr250_imp.h", "CR250_MAX_LINE", native_protocol.NATIVE_TEXT_MAX_BYTES),
    (_SPECTRO + "pr655_imp.h", "PR655_MAX_LINE", native_protocol.NATIVE_TEXT_MAX_BYTES),
    (_SPECTRO + "cr250_imp.h", "CR250_MAX_RECORDS", native_protocol.NATIVE_CR_CONTROL_MAX_ROWS),
    (_SPECTRO + "cr250_imp.h", "CR250_MAX_RESPONSE", native_protocol.NATIVE_RAW_MAX_BYTES),
    (_SPECTRO + "pr655_imp.h", "PR655_MAX_RESPONSE", native_protocol.NATIVE_RAW_MAX_BYTES),
    (_NATIVE + "cr250_host.py", "CM_CR250_RAW_EXCHANGES", native_protocol.NATIVE_RAW_MAX_EXCHANGES),
    (_NATIVE + "pr655_control.h", "CM_PR655_MAX_EXCHANGES",
     native_protocol.NATIVE_RAW_MAX_EXCHANGES),
    (_NATIVE + "cr250_host.py", "CM_CR250_RAW_BYTES", native_protocol.NATIVE_RAW_MAX_TOTAL_BYTES),
    (_NATIVE + "pr655_control.h", "CM_PR655_MAX_TRACE_BYTES",
     native_protocol.NATIVE_RAW_MAX_TOTAL_BYTES),
    (_NATIVE + "cr250_support.c", "CM_RAW_CHUNK", native_protocol.NATIVE_RAW_CHUNK_BYTES),
    (_NATIVE + "pr655_support.c", "PR_RAW_CHUNK", native_protocol.NATIVE_RAW_CHUNK_BYTES),
    (_SPECTRO + "pr655_imp.h", "PR655_MAX_ACCESSORIES", native_protocol.NATIVE_CONTROL_MAX_ROWS),
    (_SPECTRO + "cr250_imp.h", "CR250_MAX_SPECTRUM_LINES",
     native_protocol.NATIVE_CR_SPECTRUM_MAX_SAMPLES),
    (_NATIVE + "cr250_support.c", "CM_SPECTRUM_CHUNK",
     native_protocol.NATIVE_SPECTRUM_CHUNK_VALUES),
    (_NATIVE + "pr655_support.c", "PR_SPECTRUM_CHUNK",
     native_protocol.NATIVE_SPECTRUM_CHUNK_VALUES),
    (_NATIVE + "native_controls.h", "CM_CONTROL_TEXT_MAX", native_controls.CONTROL_ARGUMENT_LIMIT),
    (_NATIVE + "native_text.h", "CM_NATIVE_IDENTITY_TEXT_LIMIT",
     native_protocol.NATIVE_TEXT_MAX_BYTES),
)


@pytest.mark.parametrize(("path", "macro", "consumer_limit"), _PRODUCER_BOUNDS)
def test_native_producer_bounds_equal_their_consumer_constants(path, macro, consumer_limit):
    """Each C bound equals the Python bound on the same text, count or list.

    Each side sizes its own tests from its own constant, so changing one alone
    passes both suites: the consumer then refuses, or never receives, output
    the producer still emits. The C value is read from the composed source.
    """
    text = (Path(__file__).resolve().parents[1] / path).read_text()
    values = re.findall(rf"^#define\s+{macro}\s+\(?([0-9 *]+?)\)?\s*(?:/\*.*)?$",
                        text, re.MULTILINE)
    assert len(values) == 1, f"{macro} must be defined exactly once in {path}"
    assert (math.prod(int(term) for term in values[0].split("*"))
            == consumer_limit)



@pytest.mark.parametrize(("driver", "ceiling"), [("cr250", 100), ("pr655", 3)])
def test_spectrum_chunk_count_ceiling_follows_each_producers_bins(driver, ceiling):
    """A head may declare only as many chunks as its producer's largest spectrum needs:
    100 for CR's 10,000 bins, 3 for PR-670's 201."""
    head = dict(event="native_facts", driver=driver, identity={},
                capture=dict(reply_count=0, spectrum_chunk_count=ceiling))
    with pytest.raises(ProtocolError) as within:
        native_facts([head])
    assert "Malformed native facts" not in str(within.value)
    head["capture"]["spectrum_chunk_count"] = ceiling + 1
    with pytest.raises(ProtocolError, match="Malformed native facts"):
        native_facts([head])


def pr_raw_capture(model="PR-655"):
    """Use the bounded chunk shape emitted by pr655_support.c at 03986b56."""
    raw = bytes(range(256)) * 4 + b"\x00\r\n"
    head = dict(
        event="native_facts", driver="pr655", trace_overflow=False,
        identity=dict(verified=True, model=model, serial="PR-SOFTWARE", firmware="5.6"),
        capture=dict(valid=False, xyz=None, spectrum=None, reply_count=2,
                     spectrum_chunk_count=0, inventory_counts_valid=True,
                     accessory_list_valid=True, reported_accessory_count=1,
                     reported_aperture_count=3, inventory_counts_error=0,
                     accessory_list_error=0),
    )
    first = dict(
        event="native_raw", driver="pr655", command="M5", exchange_index=0,
        exchange_count=2, complete=False, error=273, byte_offset=0,
        byte_total=len(raw), raw_hex=raw[:1024].hex(),
    )
    second = dict(first, byte_offset=1024, raw_hex=raw[1024:].hex())
    empty = dict(first, command="SZ0", exchange_index=1, complete=True, error=0,
                 byte_offset=0, byte_total=0, raw_hex="")
    return [head, first, second, empty], raw


@pytest.mark.parametrize("model", ["PR-655", "PR-670"])
def test_pr_native_raw_capture_retains_exact_bytes_and_independent_status(model):
    events, raw = pr_raw_capture(model)
    original = deepcopy(events)
    facts = native_facts(events)
    assert facts["raw_replies"] == [
        dict(command="M5", complete=False, error=273, byte_total=len(raw), raw_hex=raw.hex()),
        dict(command="SZ0", complete=True, error=0, byte_total=0, raw_hex=""),
    ]
    assert "replies" not in facts  # No CR status or parsed PR reply is invented.
    assert facts["capture"]["spectrum"] is None
    assert events == original


@pytest.mark.parametrize("change", [
    "drop", "reorder", "overlap", "short", "count", "driver", "metadata",
    "bad_hex", "bool_index", "float_count", "unbounded_total", "empty_nonzero",
])
def test_pr_native_raw_capture_refuses_incomplete_or_unattributed_chunks(change):
    events, _ = pr_raw_capture()
    if change == "drop":
        del events[2]
    elif change == "reorder":
        events[1], events[2] = events[2], events[1]
    elif change == "overlap":
        events[2]["byte_offset"] -= 1
    elif change == "short":
        events[2]["raw_hex"] = events[2]["raw_hex"][:-2]
    elif change == "count":
        events[2]["exchange_count"] = 3
    elif change == "driver":
        events[2]["driver"] = "cr250"
    elif change == "metadata":
        events[2]["complete"] = True
    elif change == "bad_hex":
        events[2]["raw_hex"] = "not hex"
    elif change == "bool_index":
        events[3]["exchange_index"] = True
    elif change == "float_count":
        events[2]["exchange_count"] = 2.0
    elif change == "unbounded_total":
        events[1]["byte_total"] = NATIVE_RAW_MAX_BYTES + 1
    else:
        events[2]["raw_hex"] = ""
    original = deepcopy(events)
    with pytest.raises(ProtocolError):
        native_facts(events)
    assert events == original


def test_pr_native_raw_requires_its_counted_head():
    events, _ = pr_raw_capture()
    for broken in (events[1:], [dict(events[0], capture={}), *events[1:]],
                   [dict(events[0], driver="cr250"), *events[1:]]):
        with pytest.raises(ProtocolError):
            native_facts(broken)


def test_pr_native_raw_accepts_complete_declared_bound_without_truncation():
    events, _ = pr_raw_capture()
    head = deepcopy(events[0])
    count = NATIVE_RAW_MAX_TOTAL_BYTES // NATIVE_RAW_MAX_BYTES
    head["capture"]["reply_count"] = count
    raw = bytes(range(256)) * (NATIVE_RAW_MAX_BYTES // 256)
    chunks = [dict(
        events[1], exchange_index=index, exchange_count=count, byte_offset=offset,
        byte_total=len(raw), raw_hex=raw[offset:offset + NATIVE_RAW_CHUNK_BYTES].hex(),
    ) for index in range(count) for offset in range(0, len(raw), NATIVE_RAW_CHUNK_BYTES)]
    facts = native_facts([head, *chunks])
    assert len(facts["raw_replies"]) == count
    assert all(bytes.fromhex(reply["raw_hex"]) == raw for reply in facts["raw_replies"])
    with pytest.raises(ProtocolError):
        native_facts([dict(head, capture=dict(head["capture"],
                                              reply_count=NATIVE_RAW_MAX_EXCHANGES + 1)),
                      *chunks])


def test_pr_raw_aggregate_ceiling_includes_empty_exchanges():
    events, _ = pr_raw_capture()
    head = events[0]
    count = NATIVE_RAW_MAX_EXCHANGES
    head["capture"]["reply_count"] = count
    empty = dict(events[-1], command="D112", exchange_count=count)
    chunks = [dict(empty, exchange_index=index) for index in range(count)]
    assert len(native_facts([head, *chunks])["raw_replies"]) == count
    # Four full replies reach the aggregate ceiling; a fifth advertised reply
    # must fail before its large payload is accumulated.
    head["capture"]["reply_count"] = 5
    raw = b"x" * NATIVE_RAW_CHUNK_BYTES
    chunks = [dict(events[1], exchange_index=index, exchange_count=5,
                   byte_offset=offset, byte_total=NATIVE_RAW_MAX_BYTES, raw_hex=raw.hex())
              for index in range(4)
              for offset in range(0, NATIVE_RAW_MAX_BYTES, NATIVE_RAW_CHUNK_BYTES)]
    chunks.append(dict(events[1], exchange_index=4, exchange_count=5,
                       byte_offset=0, byte_total=1, raw_hex="78"))
    with pytest.raises(ProtocolError, match="aggregate"):
        native_facts([head, *chunks])


def cr_raw_capture(model="CR-250"):
    events, raw = pr_raw_capture()
    events[0] = dict(
        event="native_facts", driver="cr250", trace_overflow=False,
        identity=dict(verified=True, model=model, serial="CR-SOFTWARE", firmware="1.34"),
        capture=dict(valid=False, xyz=None, spectrum=None, reply_count=0,
                     raw_exchange_count=2, record_count=0, spectrum_chunk_count=0),
    )
    for event in events[1:]:
        event["driver"] = "cr250"
    events[1]["command"] = events[2]["command"] = "RC " + "x" * 61
    events[-1]["command"] = "RC Accessory"
    return events, raw


@pytest.mark.parametrize("model", ["CR-100", "CR-250", "CR-300"])
def test_cr_raw_retains_malformed_and_empty_exchanges_without_parsed_status(model):
    events, raw = cr_raw_capture(model)
    events[0]["trace_overflow"] = True
    original = deepcopy(events)
    facts = native_facts(events)
    assert facts["raw_replies"][0] == dict(
        command="RC " + "x" * 61, complete=False, error=273,
        byte_total=len(raw), raw_hex=raw.hex(),
    )
    assert facts["raw_replies"][1]["raw_hex"] == ""
    assert facts["replies"] == [] and facts["trace_overflow"] is True
    assert facts["capture"]["spectrum"] is None and events == original


@pytest.mark.parametrize("change", [
    "drop", "attribution", "count", "command", "overflow", "uncounted",
])
def test_cr_raw_refuses_partial_or_unattributed_serialization(change):
    events, _ = cr_raw_capture()
    if change == "drop":
        del events[2]
    elif change == "attribution":
        events[2]["complete"] = True
    elif change == "count":
        events[0]["capture"]["raw_exchange_count"] = 3
    elif change == "command":
        events[1]["command"] += "x"
    elif change == "overflow":
        events[0]["trace_overflow"] = 0
    else:
        del events[0]["capture"]["raw_exchange_count"]
    with pytest.raises(ProtocolError):
        native_facts(events)


def cr_record_capture():
    events, _ = cr_raw_capture()
    events[0]["capture"].update(record_count=2, setting_count=1)
    record = dict(
        event="native_record", driver="cr250", record_index=0, record_count=2,
        role="matched", slot=0,
        record=dict(key="Accessory", id=2**53 - 1, name="A" * 4000, type="Radiance"),
    )
    range_record = dict(record, record_index=1, role="range", slot=0,
                        record=dict(key="Range", id=3, name="D", type=None))
    setting = dict(
        event="native_setting", driver="cr250", setting_index=0, setting_count=1,
        key="Accessory", value="A" * 4000, presence="present", driver_set=False,
    )
    return [*events, record, range_record, setting]


def test_cr_capture_records_reconstruct_saved_fields_without_mutating_events():
    events = cr_record_capture()
    original = deepcopy(events)
    facts = native_facts(events)
    assert facts["capture"]["matched_records"] == [events[-3]["record"]]
    assert facts["capture"]["range_record"] == events[-2]["record"]
    assert events == original


@pytest.mark.parametrize("change", [
    "drop", "index", "count", "role", "slot", "key", "id", "text", "driver", "inline",
])
def test_cr_capture_record_attribution_and_envelope_are_required(change):
    events = cr_record_capture()
    if change == "drop":
        del events[-2]
    elif change in {"index", "count", "role", "slot", "driver"}:
        field = {"index": "record_index", "count": "record_count"}.get(change, change)
        events[-3][field] = {"index": True, "count": 3, "role": "other", "slot": 1,
                             "driver": "pr655"}[change]
    elif change == "key":
        events[-3]["record"]["key"] = "Filter"
    elif change == "id":
        events[-3]["record"]["id"] = 2**53
    elif change == "text":
        events[-3]["record"]["name"] = "A" * 4096
    else:
        events[0]["capture"]["matched_records"] = []
    with pytest.raises(ProtocolError):
        native_facts(events)
    with pytest.raises(ProtocolError):
        native_facts([events[-3]])


def test_native_control_request_keeps_zero_and_source_order_in_an_immutable_snapshot():
    changes = dict(speedModeCode=0, cycles=2)
    requested = NativeControls.from_changes("pr655", changes)
    changes["speedModeCode"] = 3
    assert requested.argument() == "cycles=2,speedModeCode=0"
    assert requested.present == (1 << 1) | (1 << 14)
    assert requested.flags() == dict(
        controls_action="apply", controls_fields="cycles=2,speedModeCode=0",
    )
    assert NativeControls.from_changes("pr655", {}).flags() == dict(
        controls_action="apply", controls_fields="",
    )
    frequency = NativeControls.from_changes("cr250", dict(syncFrequencyHz=47.25))
    assert frequency.argument() == "syncFrequencyHz=47.25"
    # PR SK takes only whole Hz from 20 to 400 (pr655_imp.c:722-725).
    assert NativeControls.from_changes("pr655", dict(syncFrequencyHz=400)).argument() == (
        "syncFrequencyHz=400")
    for refused in (47.25, 60.0, 19, 401):
        with pytest.raises(ValueError):
            NativeControls.from_changes("pr655", dict(syncFrequencyHz=refused))


def test_pr_frequency_readback_stays_decimal_while_its_request_is_whole_hz():
    # D602 reports decimal Hertz (pr655_imp.c:582); SK takes whole Hz only.
    events = pr_control_events()
    events[0]["before"] = control_values("pr655", syncFrequencyHz=59.94)
    events[0]["values"] = control_values("pr655", syncFrequencyHz=59.94)
    assert native_control_facts(events)["values"]["syncFrequencyHz"] == 59.94
    events[0]["requested"] = control_values("pr655", syncFrequencyHz=59.94, speedModeCode=0)
    with pytest.raises(ProtocolError, match="Malformed requested native controls"):
        native_control_facts(events)


@pytest.mark.parametrize("driver,changes", [
    ("other", {}), ([], {}), ("pr655", []), ("pr655", {"arbitraryCommand": 0}),
    ("pr655", {"speedModeCode": True}), ("pr655", {"cycles": 2.0}),
    ("pr655", {"cycles": -1}), ("pr655", {"cycles": 2**31}),
    ("cr250", {"matrixId": 2**53}), ("cr250", {"exposureMs": True}),
    ("cr250", {"exposureMs": float("nan")}),
    ("cr250", {"exposureMs": float("inf")}),
    ("cr250", {"exposureMs": -1}), ("cr250", {"exposureMs": 10**400}),
])
def test_native_control_request_refuses_invalid_fields_before_dispatch(driver, changes):
    with pytest.raises(ValueError):
        NativeControls.from_changes(driver, changes)


def test_native_control_request_constructor_cannot_bypass_unique_ordered_fields():
    for fields in ([('cycles', 2)], (("cycles", 1), ("cycles", 2)),
                   (("speedModeCode", 0), ("cycles", 2)), (("unknown", 1),)):
        with pytest.raises(ValueError):
            NativeControls("pr655", fields)


def pr_identity(model="PR-655"):
    count, step, pixels = (101, 4, 128) if model == "PR-655" else (201, 2, 256)
    return dict(
        driver="pr655", native_controls=True,
        identity=dict(verified=True, model=model, serial="PR-SOFTWARE", firmware="5.6",
                      grid=dict(count=count, start_nm=380, end_nm=780, step_nm=step,
                                pixels=pixels, first_pixel=0, last_pixel=pixels - 1,
                                bandwidth_nm=4.0)),
    )


@pytest.mark.parametrize("model", ["PR-655", "PR-670"])
def test_current_pr_identity_keeps_established_grid_and_control_support(model):
    packet = pr_identity(model)
    original = deepcopy(packet)
    assert validate_native_identity(packet, serial="PR-SOFTWARE") == original
    assert packet == original


def test_current_pr_identity_retains_variable_text_without_weakening_serial_pin():
    packet = pr_identity("PR-670")
    packet["identity"].update(serial="S" * 4000, firmware="F" * 4000)
    assert validate_native_identity(packet, serial="S" * 4000) == packet
    with pytest.raises(ProtocolError):
        validate_native_identity(packet, serial="S" * 3999)
    packet["identity"]["firmware"] = "F" * 4097
    with pytest.raises(ProtocolError):
        validate_native_identity(packet, serial="S" * 4000)


@pytest.mark.parametrize("change", ["model", "count", "step", "pixels", "range", "serial",
                                    "verified", "bandwidth", "controls"])
def test_current_pr_identity_refuses_model_grid_serial_and_feature_fabrication(change):
    packet = pr_identity()
    identity = packet["identity"]
    if change == "model":
        identity["model"] = "PR-670"
    elif change in {"count", "pixels"}:
        identity["grid"][change] += 1
    elif change == "step":
        identity["grid"]["step_nm"] = 2
    elif change == "range":
        identity["grid"]["last_pixel"] = 128
    elif change == "serial":
        identity["serial"] = "different"
    elif change == "verified":
        identity["verified"] = 1
    elif change == "bandwidth":
        identity["grid"]["bandwidth_nm"] = float("nan")
    else:
        packet["native_controls"] = 1
    with pytest.raises(ProtocolError):
        validate_native_identity(packet, serial="PR-SOFTWARE")


def pr_spectrum_capture(model="PR-655", *, published=False, ambient=False):
    """Native M5 completed; D2 can remain incomplete without erasing the bins."""
    count, step = (101, 4) if model == "PR-655" else (201, 2)
    events, _ = pr_raw_capture(model)
    capture = events[0]["capture"]
    capture.update(
        valid=published, native_complete=published, geometry_valid=published,
        spectrum_complete=True, spectrum_parsed=True, xyz_complete=published,
        xyz_parsed=published,
        spectrum=dict(start_nm=380, end_nm=780, step_nm=step, count=count,
                      values=[-0.0] + [0.0001] * (count - 1), unit=None,
                      inferred_unit=("W/(m^2.nm)" if ambient else "W/(sr.m^2.nm)")
                      if published else None,
                      unit_basis="same-capture driver model" if published else None,
                      published_unit=("mW/(m^2.nm)" if ambient else "mW/(m^2.sr.nm)")
                      if published else None,
                      published_scale=1000 if published else None),
    )
    events[0]["unit_check"] = dict(verdict=1 if published else 0)
    # This fixture tests qualification after chunks have already joined.
    facts = events[0].copy()
    facts.pop("event")
    return facts


@pytest.mark.parametrize("model", ["PR-655", "PR-670"])
def test_independently_complete_pr_spectrum_survives_d2_and_mapping_refusal(model):
    import math

    from color_measurement.instrument import _pr_spectral_basis

    facts = pr_spectrum_capture(model)
    original = deepcopy(facts)
    spectrum = _pr_spectral_basis(facts)
    assert spectrum["published_unit"] is None and spectrum["inferred_unit"] is None
    assert math.copysign(1, spectrum["values"][0]) == -1
    assert not facts["capture"]["native_complete"]
    assert facts == original


@pytest.mark.parametrize("change", ["count", "step", "model", "parsed", "native_unit",
                                    "inference", "publication", "denominator", "basis"])
def test_pr_spectrum_refuses_grid_and_qualification_fabrication(change):
    from color_measurement.instrument import _pr_spectral_basis

    facts = pr_spectrum_capture(published=True)
    capture, grid = facts["capture"], facts["capture"]["spectrum"]
    if change == "count":
        grid["count"] -= 1
    elif change == "step":
        grid["step_nm"] = 2
    elif change == "model":
        facts["identity"]["model"] = "PR-670"
    elif change == "parsed":
        capture["spectrum_parsed"] = False
    elif change == "native_unit":
        grid["unit"] = "W/(sr.m^2.nm)"
    elif change == "inference":
        grid["inferred_unit"] = None
    elif change == "publication":
        facts["unit_check"]["verdict"] = 3
    elif change == "denominator":
        grid["inferred_unit"] = "W/(m^2.nm)"
    else:
        grid["unit_basis"] = None
    with pytest.raises(ProtocolError):
        _pr_spectral_basis(facts)


@pytest.mark.parametrize("ambient", [False, True])
def test_pr_dim_publication_keeps_manual_model_separate_from_inferred_native_density(ambient):
    from color_measurement.instrument import _pr_spectral_basis

    facts = pr_spectrum_capture(published=True, ambient=ambient)
    facts["unit_check"]["verdict"] = 2
    grid = facts["capture"]["spectrum"]
    grid["inferred_unit"] = None
    grid["unit_basis"] = "PR manual M5 integral under established D116 geometry"
    assert _pr_spectral_basis(facts) == grid
    assert grid["unit"] is None and grid["inferred_unit"] is None
    assert grid["published_scale"] == 1000


def control_values(driver, **changes):
    names = CONTROL_FIELDS[driver]
    return dict(present=sum(1 << names.index(name) for name in changes),
                **{name: changes.get(name) for name in names})


def pr_control_events():
    setup = dict(
        primary=1, addons=[-1, -1, -1], aperture=1, units=1, exposure_mode=0,
        exposure_ms=0, gain=1, cycles=1, observer=2, dark_mode=0, sync_mode=1,
        capture_mode=0, sync_value=0.0, frequency_hz=0.0, adaptive_known=True,
        adaptive=True, sensitivity_known=True, extended=False,
        descriptions=["Native description"] * 15,
    )
    head = dict(
        event="native_control", driver="pr655", status_code=0, failed=False,
        changed=1 << 14, attempted=1 << 14, acknowledged=1 << 14, confirmed=0, blocked=0,
        before=control_values("pr655", exposureMilliseconds=0, addon1Id=-1),
        values=control_values("pr655", exposureMilliseconds=0, addon1Id=-1),
        requested=control_values("pr655", speedModeCode=0),
        setup_before_valid=True, setup_valid=True, setup_before=setup,
        setup=dict(setup, gain=2), aperture_list_valid=False, aperture_count=0,
        accessory_count=0, raw_exchange_count=1, problem=None,
        inventory_counts_valid=True, accessory_list_valid=True,
        reported_accessory_count=0, reported_aperture_count=0,
        inventory_counts_error=0, accessory_list_error=0, aperture_list_error=0,
    )
    reply = dict(
        event="native_raw", driver="pr655", command="SG0", exchange_index=0,
        exchange_count=1, complete=True, error=0, byte_offset=0, byte_total=5,
        raw_hex=b"SG0\r\n".hex(),
    )
    return [head, reply]


def test_native_pr_control_keeps_ack_unknown_current_opaque_gain_and_native_absence():
    events = pr_control_events()
    original = deepcopy(events)
    report = native_control_facts(events)
    assert report["requested"]["speedModeCode"] == 0
    assert report["acknowledged"] == 1 << 14 and report["confirmed"] == 0
    assert report["values"]["speedModeCode"] is None
    assert report["before"]["addon1Id"] == report["values"]["addon1Id"] == -1
    assert report["setup_before"]["gain"] == 1 and report["setup"]["gain"] == 2
    assert report["raw_replies"][0]["raw_hex"] == b"SG0\r\n".hex()
    assert events == original


def test_pr_inventory_retains_large_exact_ids_and_variable_names():
    events = pr_control_events()
    events[0]["accessory_count"] = 1024
    events[0]["reported_accessory_count"] = 1024
    events[0]["accessory_list_valid"] = True
    for index in range(1024):
        events.append(dict(event="native_accessory", driver="pr655",
                           accessory_index=index, accessory_count=1024,
                           id=2**53 - 1 - index, name="n" * (4000 if index == 0 else 10),
                           type="Primary", photometry="Luminance", radiometry="Radiance"))
    report = native_control_facts(events)
    assert len(report["accessories"]) == 1024
    assert report["accessories"][0]["id"] == 2**53 - 1
    assert report["accessories"][0]["name"] == "n" * 4000
    changes = NativeControls.from_changes("pr655", {"primaryAccessoryId": 2**53 - 1})
    assert changes.argument() == "primaryAccessoryId=9007199254740991"
    with pytest.raises(ValueError):
        NativeControls.from_changes("pr655", {"primaryAccessoryId": 2**53})
    with pytest.raises(ValueError):
        NativeControls.from_changes("pr655", {"speedModeCode": 2**31})


def test_pr_setup_keeps_variable_ascii_description_and_wire_ceiling():
    events = pr_control_events()
    setup = events[0]["setup"]
    setup["descriptions"] = ["n" * 4000] + ["x"] * 14
    assert native_control_facts(events)["setup"]["descriptions"][0] == "n" * 4000
    setup["descriptions"][1] = "y" * 100
    with pytest.raises(ProtocolError):
        native_control_facts(events)


@pytest.mark.parametrize("unavailable", [False, True])
def test_pr_inventory_preserves_known_empty_and_unknown_without_losing_scalars(unavailable):
    events = pr_control_events()
    head = events[0]
    head["inventory_counts_valid"] = head["accessory_list_valid"] = not unavailable
    head["aperture_list_valid"] = not unavailable
    head["inventory_counts_error"] = -1035 if unavailable else 0
    result = native_control_facts(events)
    assert result["accessories"] == result["apertures"] == []
    assert result["setup"]["exposure_ms"] == 0
    assert result["inventory_counts_valid"] is (not unavailable)
    assert result["inventory_counts_error"] == (-1035 if unavailable else 0)
    head["reported_accessory_count"] = 1
    with pytest.raises(ProtocolError):
        native_control_facts(events)


def test_pr_unavailable_lists_preserve_independent_reported_counts():
    events = pr_control_events()
    events[0].update(reported_accessory_count=42, reported_aperture_count=37,
                     accessory_list_valid=False, accessory_list_error=-2000,
                     aperture_list_valid=False, aperture_list_error=-1035)
    result = native_control_facts(events)
    assert result["reported_accessory_count"] == 42
    assert result["reported_aperture_count"] == 37
    assert result["accessories"] == result["apertures"] == []


@pytest.mark.parametrize("change", ["speed_current", "shutter_confirmed", "mask", "absent",
                                    "raw_drop", "raw_count", "setup", "apertures"])
def test_native_pr_control_refuses_false_readback_and_lost_originals(change):
    events = pr_control_events()
    head = events[0]
    if change == "speed_current":
        head["values"] = control_values("pr655", speedModeCode=0)
    elif change == "shutter_confirmed":
        head["requested"] = control_values("pr655", shutterMode=0)
        head["changed"] = head["attempted"] = head["acknowledged"] = head["confirmed"] = 1 << 13
    elif change == "mask":
        head["attempted"] |= 1
    elif change == "absent":
        head["values"]["cycles"] = 0
    elif change == "raw_drop":
        del events[1]
    elif change == "raw_count":
        head["raw_exchange_count"] = 2
    elif change == "setup":
        head["setup_before_valid"] = False
    else:
        head["aperture_count"] = 1
    with pytest.raises(ProtocolError):
        native_control_facts(events)


def cr_control_events():
    head = dict(
        event="native_control", driver="cr250", status_code=0x140003, failed=True,
        changed=1 << 16, attempted=1 << 16, acknowledged=1 << 16, confirmed=0,
        blocked=(1 << 2) | (1 << 16), before=control_values("cr250"),
        values=control_values("cr250"),
        requested=control_values("cr250", maxAutoExposureMs=1000), control_count=17,
        reply_count=2, raw_exchange_count=0, trace_overflow=False,
        problem="Final readback failed after setter ACK", aperture=None,
    )
    rows = [dict(
        event="native_control_row", driver="cr250", control_index=index, control_count=17,
        id=name, kind=0, available=False, read_only=False, limits_known=False,
        choice_count=0, minimum=None, maximum=None, current_native=None,
        problem="Not established",
    ) for index, name in enumerate(CONTROL_FIELDS["cr250"])]
    ack = dict(event="native_control_reply", driver="cr250", reply_index=0, reply_count=2,
               phase=1, error=0, complete=True, command="SM MaxAutoExposure 1000",
               status="OK", code=0, context="MaxAutoExposure", value="")
    reply = dict(event="native_control_reply", driver="cr250", reply_index=1, reply_count=2,
                 phase=2, error=3, complete=False, command="RM Mode")
    return [head, *rows, ack, reply]


def test_native_cr_control_keeps_all_rows_pending_mask_and_unparsed_status():
    events = cr_control_events()
    original = deepcopy(events)
    report = native_control_facts(events)
    assert len(report["controls"]) == 17
    assert report["blocked"] == (1 << 2) | (1 << 16)
    assert report["values"]["maxAutoExposureMs"] is None and report["confirmed"] == 0
    assert report["replies"][0]["status"] == "OK"
    assert report["replies"][-1]["complete"] is False
    assert "status" not in report["replies"][-1] and "code" not in report["replies"][-1]
    assert events == original


def test_native_cr_controls_retain_raw_separately_from_parsed_replies():
    events = cr_control_events()
    raw_events, raw = cr_raw_capture()
    events[0].update(raw_exchange_count=2, trace_overflow=True)
    events.extend(raw_events[1:])
    original = deepcopy(events)
    report = native_control_facts(events)
    assert len(report["replies"]) == 2
    assert report["raw_replies"][0]["raw_hex"] == raw.hex()
    assert report["trace_overflow"] is True and events == original
    events.pop()
    with pytest.raises(ProtocolError, match="incomplete"):
        native_control_facts(events)


def test_native_cr_control_admits_its_producers_reply_bound_and_no_more():
    """cr250_control.h retains at most CM_CR250_CONTROL_REPLIES replies."""
    head, *rest = cr_control_events()
    rows = rest[:-2]

    def events(count):
        return [dict(head, reply_count=count), *rows, *(
            dict(event="native_control_reply", driver="cr250", reply_index=index,
                 reply_count=count, phase=2, error=3, complete=False, command="RM Mode")
            for index in range(count))]

    limit = native_protocol.NATIVE_CR_CONTROL_MAX_REPLIES
    # Every field may carry a control row and full inventory, followed by replies.
    assert native_protocol.NATIVE_CONTROL_MAX_EVENTS >= (
        len(native_controls.CONTROL_FIELDS["cr250"])
        * (1 + native_protocol.NATIVE_CR_CONTROL_MAX_ROWS) + limit
    )
    assert len(native_control_facts(events(limit))["replies"]) == limit
    with pytest.raises(ProtocolError, match="Invalid CR native control reply count"):
        native_control_facts(events(limit + 1))


def test_native_cr_inventory_admits_256_exact_ids():
    events = cr_control_events()
    row = events[1]
    row.update(choice_count=256, kind=1, available=True)
    choices = [dict(
        event="native_control_choice", driver="cr250", control_index=0,
        choice_index=index, choice_count=256,
        choice=dict(key=row["id"], id=2**53 - 256 + index,
                    name=f"Mode {index}", type=None),
    ) for index in range(256)]
    events[2:2] = choices
    report = native_control_facts(events)
    assert len(report["controls"][0]["choices"]) == 256
    assert report["controls"][0]["choices"][-1]["id"] == 2**53 - 1


@pytest.mark.parametrize("surface", ["choice", "capture"])
@pytest.mark.parametrize("identifier,token,quantity_type", [
    (0, "0", None), (2**53 - 1, "9007199254740991", None),
    (10**15, "1e15", None), (2**53 - 1, "9007199254740991", "Radiance"),
])
def test_cr_record_envelope_counts_wire_fields_and_compact_ids(
    surface, identifier, token, quantity_type,
):
    name = "A" * (4096 - len(token) - 1 - (
        0 if quantity_type is None else 1 + len(quantity_type)))
    wire = f"{token},{name}" + ("" if quantity_type is None else f",{quantity_type}")
    assert len(wire.encode("ascii")) == 4096
    if surface == "choice":
        events = cr_control_events()
        field = "exposureMode" if quantity_type is None else "accessory"
        index = CONTROL_FIELDS["cr250"].index(field)
        events[1 + index].update(choice_count=1, kind=0, available=True)
        item = dict(key=field, id=identifier, name=name, type=quantity_type)
        events.insert(2 + index, dict(
            event="native_control_choice", driver="cr250", control_index=index,
            choice_index=0, choice_count=1, choice=item,
        ))
        decode = native_control_facts
        assert decode(events)["controls"][index]["choices"] == [item]
    else:
        events = cr_record_capture()
        field = "ExposureMode" if quantity_type is None else "Accessory"
        item = dict(key=field, id=identifier, name=name, type=quantity_type)
        events[-3]["record"] = item
        events[-1].update(key=field, value=name)
        decode = native_facts
        assert decode(events)["capture"]["matched_records"] == [item]
    item["name"] += "A"
    with pytest.raises(ProtocolError, match="wire envelope"):
        decode(events)


@pytest.mark.parametrize("change", [
    "extra", "duplicate", "unicode", "control_byte", "id", "boolean",
])
def test_native_cr_inventory_rejects_surplus_duplicates_and_invalid_native_text(change):
    events = cr_control_events()
    events[1]["choice_count"] = 2
    choice = dict(event="native_control_choice", driver="cr250", control_index=0,
                  choice_index=0, choice_count=2,
                  choice=dict(key="mode", id=0, name="Colorimeter", type=None))
    events[2:2] = [choice, dict(choice, choice_index=1,
                              choice=dict(key="mode", id=1, name="Raw", type=None))]
    if change == "extra":
        events[1]["choice_count"] = 257
    elif change == "duplicate":
        events[3]["choice"]["id"] = 0
    elif change == "unicode":
        events[2]["choice"]["name"] = "é"
    elif change == "control_byte":
        events[2]["choice"]["name"] = "bad\x00name"
    elif change == "id":
        events[2]["choice"]["id"] = 2**53
    else:
        events[2]["choice"]["id"] = True
    with pytest.raises(ProtocolError):
        native_control_facts(events)


def test_cr_inventory_aggregate_uses_actual_lf_framing_without_crlf_guess():
    events = cr_control_events()
    row = events[1]
    row.update(choice_count=256, kind=0, available=True)
    header = b"OK:0:RC Mode:256\n"
    choices = []
    wire_rows = []
    for index in range(256):
        identifier = 2**53 - 256 + index
        name = "A" * (4095 - len(str(identifier)) - 1 - (len(header) if index == 0 else 0))
        wire_rows.append(f"{identifier},{name}\n".encode("ascii"))
        choices.append(dict(
            event="native_control_choice", driver="cr250", control_index=0,
            choice_index=index, choice_count=256,
            choice=dict(key=row["id"], id=identifier, name=name, type=None),
        ))
    wire = header + b"".join(wire_rows)
    assert len(wire) == NATIVE_RAW_MAX_BYTES
    events[2:2] = choices
    events[0]["raw_exchange_count"] = 1
    events.extend(dict(
        event="native_raw", driver="cr250", command="RC Mode", exchange_index=0,
        exchange_count=1, complete=True, error=0, byte_offset=offset,
        byte_total=len(wire), raw_hex=wire[offset:offset + NATIVE_RAW_CHUNK_BYTES].hex(),
    ) for offset in range(0, len(wire), NATIVE_RAW_CHUNK_BYTES))
    report = native_control_facts(events)
    assert len(report["controls"][0]["choices"]) == 256
    assert bytes.fromhex(report["raw_replies"][0]["raw_hex"]) == wire


@pytest.mark.parametrize("change", ["row_drop", "row_order", "choice_drop", "status",
                                    "reply_drop", "unconfirmed_current"])
def test_native_cr_control_refuses_missing_inventory_or_fabricated_ack_readback(change):
    events = cr_control_events()
    if change == "row_drop":
        del events[3]
    elif change == "row_order":
        events[1], events[2] = events[2], events[1]
    elif change == "choice_drop":
        events[1]["choice_count"] = 1
    elif change == "status":
        events[-1]["status"] = "OK"
    elif change == "reply_drop":
        events.pop()
    else:
        events[0]["values"] = control_values("cr250", maxAutoExposureMs=1000)
    with pytest.raises(ProtocolError):
        native_control_facts(events)


@pytest.mark.parametrize("driver,name,target,current", [
    ("pr655", "cycles", 3, 1), ("cr250", "exposureMs", 100, 90),
])
def test_confirmed_native_controls_require_actual_requested_readback(driver, name, target, current):
    events = pr_control_events() if driver == "pr655" else cr_control_events()
    bit = 1 << CONTROL_FIELDS[driver].index(name)
    events[0].update(
        status_code=0, failed=False, changed=bit, attempted=bit, acknowledged=bit,
        confirmed=bit, blocked=0, requested=control_values(driver, **{name: target}),
        values=control_values(driver, **{name: current}),
    )
    with pytest.raises(ProtocolError, match="requested readback"):
        native_control_facts(events)
    events[0]["values"] = control_values(driver, **{name: target})
    assert native_control_facts(events)["confirmed"] == bit
    events[0]["values"] = control_values(driver)
    with pytest.raises(ProtocolError, match="requested readback"):
        native_control_facts(events)


def scripted_session(monkeypatch, details, *, failed=False, connected=True):
    """Exercise the shared client, including its normal allowance/ending/cleanup seams."""
    from conftest import native_display_unsupported

    from color_measurement.argyll_capability import baseline_mode_words, mode_word
    from color_measurement.mode_acceptance import BASELINE_SCOPE

    primary = mode_word("emissive_spot", spectral=False)
    binding = dict(
        event="connected", serial="PR-SOFTWARE", serial_encoding="utf8", spectral=False,
        mode=primary, display_types=native_display_unsupported(),
        baseline_mode_checks=dict(scope=BASELINE_SCOPE, mode_capabilities=primary,
                                  checks=[dict(mode_word=word, native_code=0)
                                          for word in baseline_mode_words(primary)]),
    )
    messages = [dict(protocol_version=1, provenance="simulated", **event)
                for event in ([binding] if connected else []) + details + [
                    dict(event="terminal", status="failed" if failed else "completed")]]

    class Session:
        stopped = "exited"
        sent = []

        def receive_message(self, **kwargs):
            return messages.pop(0)

        def send(self, *args):
            self.sent.append(args)

        def finish_messages(self, *, expected_returncode):
            assert expected_returncode == int(failed)

        def close(self):
            pass

    session = Session()
    monkeypatch.setattr(Instrument, "_open_session", lambda *args: session)
    return session


@pytest.mark.parametrize("stage,connected", [
    ("attach", False), ("init_coms", False), ("init_inst", False),
    ("check_mode", False), ("cancelled", True), ("native_controls_fields", True),
])
def test_controls_preserve_native_failure_before_a_transaction(monkeypatch, stage, connected):
    scripted_session(monkeypatch, [dict(event="error", stage=stage, native_code=123)],
                     failed=True, connected=connected)
    with pytest.raises(InstrumentError) as raised:
        Instrument("/never-started", "instPR655").native_controls("emissive_spot", {})
    assert raised.value.stage == stage and raised.value.native_code == 123
    assert raised.value.native_controls is None and raised.value.worker_stop == "exited"


def test_controls_report_must_match_dispatched_changes_and_success_needs_a_report(monkeypatch):
    scripted_session(monkeypatch, pr_control_events())
    with pytest.raises(ProtocolError, match="dispatched changes"):
        Instrument("/never-started", "instPR670").native_controls(
            "emissive_spot", dict(speedModeCode=1),
        )
    scripted_session(monkeypatch, [])
    with pytest.raises(ProtocolError, match="complete retained report"):
        Instrument("/never-started", "instPR670").native_controls("emissive_spot")


def test_settings_refresh_uses_existing_allowance_without_an_unrecognized_cli_flag(monkeypatch):
    events = pr_control_events()
    events[0].update(changed=0, attempted=0, acknowledged=0,
                     requested=control_values("pr655"))
    session = scripted_session(monkeypatch, [dict(event="read_allowance_required"), *events])
    meter = Instrument("/never-started", "instPR670")
    flags = []
    original = meter._run
    monkeypatch.setattr(Instrument, "_run", lambda self, *args, **kw: (
        flags.append(kw) or original(*args, **kw)))
    result = meter.native_controls("emissive_spot")
    assert result.workflow == "native_controls" and not result.has_readings
    assert session.sent[0][0] == "read_allowance"
    assert 0 < float(session.sent[0][1]) <= meter.timeout
    assert "inspect_only" not in flags[0]


def native_frontend(driver="pr655", *, state=None, serial="PR-SOFTWARE", supported=True,
                    meter_serial="bound"):
    from color_measurement.argyll_capability import mode_word
    from color_measurement.instrument_frontend import InstrumentFrontend

    symbol = "instPR670" if driver == "pr655" else "instCR250"
    native = pr_identity("PR-670") if driver == "pr655" else dict(
        driver="cr250", native_controls=True,
        identity=dict(verified=True, model="CR-250", serial=serial, firmware="1.26",
                      firmware_components=[1, 26], instrument_type=2),
    )
    native["identity"]["serial"] = serial
    meter = Instrument("/never-started", symbol,
                       expected_serial=serial if meter_serial == "bound" else meter_serial,
                       expected_helper_sha256="0" * 64, timeout=400)
    return InstrumentFrontend(
        meter, mode_capabilities=mode_word("emissive_spot", spectral=False),
        trigger_capabilities=1, filter_capabilities=0, provenance="simulated", serial=serial,
        current_binding_identity=dict(
            device_id=meter.instrument, initialized_type=41, native_identity=native,
            provenance=dict(backend_version="3.5.0", description_helper_sha256="1" * 64,
                            session_helper_sha256="0" * 64, build_report_sha256=None),
        ),
        native_controls_available=supported, native_control_state=state,
        read_allowance_floor=310,
    )


def native_readback_seam(monkeypatch, front):
    """Substitute only the device result; keep real frontend/batch/desktop owners.

    These typed synthetic inputs exercise application integration, not the C
    driver or equipment. Same-capture setup is independent of the later cache.
    """
    from test_instrument import _measurement, _patch

    from color_measurement.native_controls import native_control_driver

    driver = native_control_driver(front._identity.model)
    state = dict(fail_next=False, measurements=0,
                 current=(dict(cycles=1, apertureId=1) if driver == "pr655"
                                         else dict(exposureMs=10)))

    def completed(meter, mode, changes, *, settings=False):
        if not settings:
            state["measurements"] += 1
        requested = NativeControls.from_changes(driver, changes or {})
        before = deepcopy(state["current"])
        failed = state["fail_next"] and requested.present != 0
        state["fail_next"] = False
        changed = sum(1 << CONTROL_FIELDS[driver].index(key) for key, value in requested.fields
                      if before.get(key) != value)
        attempted = changed & -changed if failed else changed
        if not failed:
            state["current"].update(dict(requested.fields))
        events = pr_control_events() if driver == "pr655" else cr_control_events()
        head = events[0]
        head.update(
            status_code=0x140003 if failed else 0, failed=failed, changed=changed,
            attempted=attempted, acknowledged=0 if failed else attempted,
            confirmed=0 if failed else requested.present, blocked=attempted if failed else 0,
            requested=control_values(driver, **dict(requested.fields)),
            before=control_values(driver, **before),
            values=control_values(driver, **state["current"]),
        )
        if driver == "pr655":
            head["raw_exchange_count"] = 0
            events = [head]
            head["setup_before"] = dict(head["setup_before"], cycles=before["cycles"],
                                         aperture=before["apertureId"])
            head["setup"] = dict(head["setup_before"], cycles=state["current"]["cycles"],
                                  aperture=state["current"]["apertureId"])
        report = native_control_facts(events)
        if failed:
            error = InstrumentError("native_controls", 0x140003)
            error.native_controls = report
            error.events = tuple(events)
            error.worker_stop = "exited"
            raise error
        native = front._current_binding_identity["native_identity"]
        identity = deepcopy(native["identity"])
        if driver == "pr655":
            facts = pr_spectrum_capture(identity["model"], published=True)
            facts["identity"] = {key: value for key, value in identity.items() if key != "grid"}
            facts["capture"].update(
                spectrum=None, spectrum_complete=False, spectrum_parsed=False,
                xyz=[21.5, 24, 9.75], xyz_publication=dict(
                    xyz=[21.5, 24, 9.75], unit="cd/m2", observer=2,
                    native_to_published_scale=1),
                setup_before=deepcopy(head["setup"]), setup_after=deepcopy(head["setup"]),
            )
        else:
            facts = dict(driver=driver, identity=identity,
                         capture=dict(valid=True, xyz=[21.5, 24, 9.75], spectrum=None))
        return _measurement(
            instrument=meter.instrument, mode=mode,
            workflow="native_controls" if settings else "spot",
            serial=front._identity.serial, provenance="simulated", spectral=False,
            confirmed_routes=(mode,), patches=() if settings else (_patch(mtype=1),),
            calibration=(None if settings else
                         dict(remaining_calibrations=0, performed=False, status_source="driver")),
            native_controls=report if settings or changes is not None else None,
            native_facts=None if settings else facts, events=tuple(events), worker_stop="exited",
        )

    monkeypatch.setattr(Instrument, "measure", lambda meter, mode, **kwargs: completed(
        meter, mode, None if kwargs["configuration"].native_controls is None
        else dict(kwargs["configuration"].native_controls.fields)))
    monkeypatch.setattr(Instrument, "native_controls", lambda meter, mode, changes=None, **kwargs:
                        completed(meter, mode, changes, settings=True))
    return state


def controls_request(front, *, workflow="native_controls", changes=None):
    descriptor = json.loads(front.describe_json())
    return json.dumps(dict(
        schema_version=1, device_id=descriptor["device_id"],
        capability_id=descriptor["capability_id"], mode="emissive_spot", workflow=workflow,
        patch_ids=["P1"] if workflow == "spot" else [],
        options={} if changes is None else dict(native_controls=changes),
        layout={} if workflow == "spot" else dict(action="read" if changes is None else "apply"),
    ))


@pytest.mark.parametrize("driver", ["pr655", "cr250"])
def test_recovery_survives_read_and_rebind_and_requires_complete_explicit_apply(driver):
    state = NativeControlState()
    front = native_frontend(driver, state=state)
    changes = (dict(cycles=3, speedModeCode=0) if driver == "pr655"
               else dict(exposureMs=100, maxAutoExposureMs=1000))
    request = NativeControls.from_changes(driver, changes)
    report = native_control_facts(pr_control_events() if driver == "pr655" else cr_control_events())
    report.update(failed=True, attempted=request.present, blocked=request.present)
    front._retain_native_controls(report, request)
    read = dict(report, failed=False, attempted=0, blocked=0, confirmed=0, acknowledged=0)
    front._retain_native_controls(read, None)
    replacement = native_frontend(driver, state=state)
    assert replacement._control_pending() == request.present
    assert replacement._ownership is front._ownership
    for incomplete in ({}, {next(iter(changes)): next(iter(changes.values()))}):
        with pytest.raises(ValueError, match="every unresolved field"):
            replacement.prepare(controls_request(replacement, changes=incomplete), timeout=30)
    with pytest.raises(ValueError, match="every unresolved field"):
        replacement.prepare(controls_request(replacement, workflow="spot"), timeout=400)
    # A non-acquiring refresh may use 30 seconds despite the actual read's 310-second floor.
    replacement.prepare(controls_request(replacement), timeout=30)
    ack_only = (1 << 14) if driver == "pr655" else (1 << 16)
    success = dict(report, failed=False, blocked=0, confirmed=request.present & ~ack_only,
                   acknowledged=ack_only)
    replacement._retain_native_controls(success, request)
    assert front._control_pending() == replacement._control_pending() == 0
    other = native_frontend(driver, state=state, serial="OTHER")
    front._retain_native_controls(None, request, uncertain=True)
    assert other._control_pending() == 0
    assert other._ownership is not front._ownership


def test_debt_change_after_preparation_refuses_dispatch_without_io(monkeypatch):
    front = native_frontend()
    operation = front.prepare(controls_request(front, workflow="spot"), timeout=400)
    request = NativeControls.from_changes("pr655", dict(cycles=3))
    front._retain_native_controls(None, request, uncertain=True)
    monkeypatch.setattr(Instrument, "_open_session", lambda *args: pytest.fail("Device I/O"))
    operation.dispatch()
    assert operation.done
    assert "every unresolved field" in json.loads(operation.result_json())["error"]


def test_zero_patch_control_record_keeps_originals_without_a_measurement(monkeypatch, tmp_path):
    from color_measurement.operation_exports import load, save
    from color_measurement.operation_records import result_from_json

    events = pr_control_events()
    events[0].update(changed=0, attempted=0, acknowledged=0,
                     requested=control_values("pr655"))
    scripted_session(monkeypatch, [dict(event="read_allowance_required"), *events])
    front = native_frontend()
    operation = front.prepare(controls_request(front), timeout=30)
    operation.dispatch()
    deadline = time.monotonic() + 3
    while not operation.done:
        assert time.monotonic() < deadline
        time.sleep(0.005)
    result = json.loads(operation.result_json())
    assert result["status"] == "completed", result["error"]
    assert result["patches"] == []
    retained = json.loads(dict(result["configuration"])["native_control_json"])
    assert retained["raw_replies"][0]["raw_hex"] == b"SG0\r\n".hex()
    assert retained["values"]["speedModeCode"] is None
    assert json.loads(front.describe_json())["native_controls"]["current"]["setup"]["gain"] == 2
    path = tmp_path / "settings.json"
    record = result_from_json(operation.result_json())
    save(record, path)
    assert load(path) == record


def test_cleanup_wrapped_native_failure_keeps_only_attempted_control_debt(monkeypatch):
    events = pr_control_events()
    request = dict(cycles=3, speedModeCode=0)
    events[0].update(
        status_code=0x140003, failed=True, requested=control_values("pr655", **request),
        changed=(1 << 1) | (1 << 14), attempted=1 << 1, acknowledged=0,
        blocked=1 << 1,
    )
    session = scripted_session(monkeypatch, [*events, dict(
        event="error", stage="native_controls", native_code=0x140003,
    )], failed=True)

    def cleanup_error():
        raise OSError("Owned cleanup failed after the validated control report")

    monkeypatch.setattr(session, "close", cleanup_error)
    front = native_frontend()
    operation = front.prepare(controls_request(front, changes=request), timeout=30)
    operation.dispatch()
    deadline = time.monotonic() + 3
    while not operation.done:
        assert time.monotonic() < deadline
        time.sleep(0.005)
    result = json.loads(operation.result_json())
    assert result["status"] == "failed" and result["error_stage"] == "native_controls"
    assert front._control_pending() == 1 << 1
    retained = json.loads(dict(result["configuration"])["native_control_json"])
    assert retained["attempted"] == 1 << 1
    assert retained["raw_replies"][0]["raw_hex"] == b"SG0\r\n".hex()
    assert "native_control_exchange_json" not in dict(result["configuration"])


@pytest.mark.parametrize("model,native_type", [("PR-655", 41), ("PR-670", 42)])
def test_current_pr_identity_describes_and_binds_in_its_native_namespace(model, native_type):
    from test_discovery import _build, _candidates

    catalog = json.dumps(dict(instrument_types=[dict(
        enum="inst" + model.replace("-", ""), native_type=native_type,
        source_label="Photo Research " + model, initialization_assessed=True,
        initialization_refusal="initialization_enters_remote_mode",
        native_facts=True, native_controls=True, typed_serial_selection=True,
        generic_serial_identification=False,
    )]))
    build, client = _build(
        _candidates(("selected-pr-path", native_type, 1, "initialization_enters_remote_mode")),
        catalog=catalog, describe=dict(
            initialized_dtype=native_type, serial="PR-SOFTWARE",
            native_identity=pr_identity(model), provenance="simulated",
            capabilities=dict(primary=0x21, secondary=1, tertiary=0),
        ),
    )
    candidate, = build.attached()
    with pytest.raises(ValueError, match="initialization"):
        build.describe(candidate)
    description = build.describe(candidate, accept_disclosed_initialization=True)
    front = build.bind(description)
    packet = json.loads(front.describe_json())
    assert packet["device"]["model"] == "Photo Research " + model
    assert packet["current_binding_identity"]["native_identity"] == pr_identity(model)
    assert packet["native_controls"]["available"] is True
    assert client.enumerations == 1 and len(client.describes) == 1


def test_control_descriptor_exposes_typed_members_without_guessed_settings():
    descriptor = json.loads(native_frontend().describe_json())
    option = next(row for row in descriptor["options"] if row["id"] == "native_controls")
    assert option["type"] == "object" and option["default"] is None
    members = {row["key"]: row for row in option["members"]}
    assert tuple(members) == CONTROL_FIELDS["pr655"]
    frequency = members["syncFrequencyHz"]
    assert (frequency["type"], frequency["minimum"], frequency["maximum"]) == ("integer", 20, 400)
    assert frequency["unit"] == "Hz" and "Zero is explicit" not in frequency["help"]
    assert "Whole Hz from 20 to 400" in frequency["help"]
    assert "Measurement publication requires 2" in members["instrumentObserver"]["help"]
    cr = next(row for row in json.loads(native_frontend("cr250").describe_json())["options"]
              if row["id"] == "native_controls")
    assert next(row for row in cr["members"] if row["key"] == "syncFrequencyHz")[
        "type"] == "number"
    assert members["speedModeCode"]["type"] == "integer"
    assert all(row["default"] is None for row in members.values())
    assert descriptor["native_controls"]["current"] is None
    assert "native_controls" not in json.loads(native_frontend(supported=False).describe_json())
    with pytest.raises(ValueError):
        front = native_frontend(supported=False)
        front.prepare(controls_request(front, workflow="spot", changes=dict(cycles=3)), timeout=400)


def test_settings_and_measurement_raw_envelopes_keep_independent_exchange_indices():
    controls = pr_control_events()
    capture, raw = pr_raw_capture()
    report = native_control_facts([*controls, *capture])
    facts = native_facts([*controls, *capture])
    assert report["raw_replies"][0]["raw_hex"] == b"SG0\r\n".hex()
    assert facts["raw_replies"][0]["raw_hex"] == raw.hex()


@pytest.mark.parametrize("stray", [
    "raw_before_head", "record_before_head", "record_in_settings", "choice_in_capture",
])
def test_native_detail_outside_its_counted_report_is_refused_not_dropped(stray):
    # session.c emits each detail after its own head: settings rows/choices/replies
    # after native_control, capture lists/records after native_facts, raw after
    # either. Each stray below was previously skipped by both joiners.
    controls = cr_control_events()
    capture = cr_record_capture()
    assert native_facts([*controls, *capture])["capture"]["range_record"]["name"] == "D"
    assert len(native_control_facts([*controls, *capture])["controls"]) == 17
    if stray == "raw_before_head":
        events = [dict(capture[1], exchange_index=9, raw_hex="zz"), *capture]
    elif stray == "record_before_head":
        events = [dict(capture[-3], record=None), *capture]
    elif stray == "record_in_settings":
        events = [*controls, dict(capture[-3], record_index=7), *capture]
    else:
        choice = dict(event="native_control_choice", driver="cr250", control_index=0,
                      choice_index=0, choice_count=1,
                      choice=dict(key="mode", id=0, name="Colorimeter", type=None))
        events = [*controls, *capture, choice]
    for join in (native_facts, native_control_facts):
        with pytest.raises(ProtocolError, match="outside its counted report"):
            join(events)


def test_cr_trace_overflow_cannot_accompany_a_successful_report():
    # cr250_control.c finish() fails an otherwise successful report on overflow,
    # and an exchange whose snapshot fails returns that error before c->valid.
    from test_instrument import cr_qualified_facts

    events = cr_control_events()
    bit = 1 << CONTROL_FIELDS["cr250"].index("exposureMs")
    events[0].update(
        status_code=0, failed=False, changed=bit, attempted=bit, acknowledged=bit,
        confirmed=bit, blocked=0, requested=control_values("cr250", exposureMs=100),
        values=control_values("cr250", exposureMs=100),
    )
    assert native_control_facts(events)["trace_overflow"] is False
    events[0]["trace_overflow"] = True
    with pytest.raises(ProtocolError, match="overflow cannot accompany"):
        native_control_facts(events)
    facts = cr_qualified_facts()
    assert native_facts([dict(facts, event="native_facts")])["capture"]["valid"] is True
    facts["trace_overflow"] = True
    with pytest.raises(ProtocolError, match="overflow cannot accompany"):
        native_facts([dict(facts, event="native_facts")])
    facts = cr_qualified_facts(verdict="not_checked", published=False)
    facts["trace_overflow"] = True
    assert native_facts([dict(facts, event="native_facts")])["trace_overflow"] is True


@pytest.mark.parametrize("model", ["PR-655", "PR-670"])
def test_pr_capture_trace_overflow_is_checked_like_cr(model):
    # pr655_support.c emits trace_overflow on every capture; pr655_control.c ends
    # an overflowed capture with CM_PR655_TRACE_LIMIT, so it is never valid.
    events, _ = pr_raw_capture(model)
    events[0]["trace_overflow"] = True
    assert native_facts(deepcopy(events))["trace_overflow"] is True
    for value in (None, 0, "false"):
        events[0]["trace_overflow"] = value
        with pytest.raises(ProtocolError, match="Invalid native trace overflow"):
            native_facts(deepcopy(events))
    del events[0]["trace_overflow"]
    with pytest.raises(ProtocolError, match="Invalid native trace overflow"):
        native_facts(deepcopy(events))
    events[0].update(trace_overflow=True)
    events[0]["capture"]["valid"] = True
    with pytest.raises(ProtocolError, match="overflow cannot accompany"):
        native_facts(deepcopy(events))


def test_runtime_controls_do_not_change_capability_but_material_features_still_do():
    from color_measurement.instrument_frontend import capability_identity, descriptor_context

    front = native_frontend()
    before = json.loads(front.describe_json())
    report = native_control_facts(pr_control_events())
    front._retain_native_controls(
        report, NativeControls.from_changes("pr655", dict(speedModeCode=0)),
    )
    front._retain_native_controls(None, NativeControls.from_changes("pr655", dict(cycles=3)),
                                  uncertain=True)
    after = json.loads(front.describe_json())
    assert after["native_controls"]["current"] is not None
    assert after["native_controls"]["pending"] == 1 << 1
    assert after["capability_id"] == before["capability_id"]
    assert after["current_binding_identity"] == before["current_binding_identity"]
    assert descriptor_context(after) == descriptor_context(before)
    for field, value in (("available", False), ("driver", "cr250"), ("fields", ["other"])):
        changed = deepcopy(after)
        changed.pop("capability_id")
        changed["native_controls"][field] = value
        assert capability_identity(changed) != before["capability_id"]
    changed = deepcopy(after)
    changed.pop("capability_id")
    changed["current_binding_identity"]["native_identity"]["identity"]["grid"]["bandwidth_nm"] += 1
    assert capability_identity(changed) != before["capability_id"]
    receipts = deepcopy(after)
    receipts["current_binding_identity"]["provenance"]["session_helper_sha256"] = "f" * 64
    assert descriptor_context(receipts) != descriptor_context(after)


@pytest.mark.parametrize("field", ["model", "grid", "serial", "support"])
def test_chart_current_identity_reuses_shared_native_admission(field):
    from color_measurement.chart_context import intended
    from color_measurement.instrument_frontend import capability_identity

    front = native_frontend()
    descriptor = json.loads(front.describe_json())
    native = descriptor["current_binding_identity"]["native_identity"]
    if field == "model":
        native["identity"]["model"] = "PR-655"
    elif field == "grid":
        native["identity"]["grid"]["count"] = 101
    elif field == "serial":
        native["identity"]["serial"] = "OTHER"
    else:
        native["native_controls"] = 1
    descriptor["capability_id"] = capability_identity({
        key: value for key, value in descriptor.items() if key != "capability_id"
    })
    request = json.loads(controls_request(front, workflow="spot"))
    request["capability_id"] = descriptor["capability_id"]
    with pytest.raises(ValueError):
        intended(descriptor, request)


@pytest.mark.parametrize("model", ["PR-655", "PR-670"])
@pytest.mark.parametrize("spectral", [False, True])
@pytest.mark.parametrize("ambient", [False, True])
def test_pr_publication_matches_actual_output_and_keeps_native_model_basis(
    model, spectral, ambient,
):
    from dataclasses import replace

    from test_instrument import _device, _measurement, _patch

    from color_measurement.instrument import observation_from_patch
    from color_measurement.native_protocol import NativeSpectrum

    facts = pr_spectrum_capture(model, published=True, ambient=ambient)
    capture = facts["capture"]
    capture["xyz"] = [1, 2, 3]
    capture["xyz_publication"] = dict(
        xyz=[1.5, 3, 4.5], unit="lux" if ambient else "cd/m2", observer=2,
        native_to_published_scale=1.5,
    )
    grid = capture["spectrum"]
    spectrum = NativeSpectrum(grid["start_nm"], grid["end_nm"], 1,
                              tuple(value * 1000 for value in grid["values"]))
    if not spectral:
        capture.update(spectrum=None, spectrum_complete=False, spectrum_parsed=False)
        spectrum = None
    patch = _patch(mtype=2 if ambient else 1, xyz=(1.5, 3, 4.5), spectrum=spectrum)
    measurement = _measurement(mode="ambient_spot" if ambient else "emissive_spot",
                               serial="PR-SOFTWARE", native_facts=facts)
    device = _device(model="Photo Research " + model, serial="PR-SOFTWARE")
    arguments = dict(device=device, operation_id="software-op", sample_id="P1",
                     acquired_at="2026-10-14T00:00:00Z", raw_backend_output="{}")
    observation = observation_from_patch(patch, measurement, **arguments)
    assert observation.backend_reported.xyz.values == patch.xyz
    assert observation.backend_reported.xyz.unit == ("lux" if ambient else "cd/m^2")
    assert observation.backend_reported.xyz.scale == (
        "absolute; Y is illuminance" if ambient else "absolute; Y is luminance")
    assert (observation.backend_reported.spectrum is not None) == spectral
    if spectral:
        assert observation.backend_reported.spectrum.quantity == (
            "spectral_irradiance" if ambient else "spectral_radiance")
        assert observation.backend_reported.spectrum.unit == (
            "mW/(m^2 nm)" if ambient else "mW/(m^2 sr nm)")
    original = deepcopy(facts)
    capture["xyz_publication"]["native_to_published_scale"] = 2
    with pytest.raises(ProtocolError, match="retained native source"):
        observation_from_patch(patch, measurement, **arguments)
    capture.update(deepcopy(original["capture"]))
    capture["xyz_publication"]["unit"] = "cd/m2" if ambient else "lux"
    with pytest.raises(ProtocolError, match="publication basis"):
        observation_from_patch(patch, measurement, **arguments)
    capture.update(deepcopy(original["capture"]))
    capture["xyz_publication"]["observer"] = 10
    with pytest.raises(ProtocolError, match="publication basis"):
        observation_from_patch(patch, measurement, **arguments)
    capture.update(deepcopy(original["capture"]))
    with pytest.raises(ProtocolError, match="publication basis"):
        observation_from_patch(replace(patch, mtype=0), measurement, **arguments)
    if spectral:
        capture["spectrum"].update(
            inferred_unit="W/(sr.m^2.nm)" if ambient else "W/(m^2.nm)",
            published_unit="mW/(m^2.sr.nm)" if ambient else "mW/(m^2.nm)",
        )
        with pytest.raises(ProtocolError, match="measurement type"):
            observation_from_patch(patch, measurement, **arguments)
        capture.update(deepcopy(original["capture"]))
    facts["identity"]["serial"] = "OTHER"
    with pytest.raises(ProtocolError, match="different reported serial"):
        observation_from_patch(patch, measurement, **arguments)


@pytest.mark.parametrize("model", ["PR-655", "PR-670"])
@pytest.mark.parametrize("ambient", [False, True])
def test_refused_pr_capture_retains_independent_quantities_without_invented_units(model, ambient):
    from test_instrument import _device

    from color_measurement.instrument import observation_from_native_capture

    facts = pr_spectrum_capture(model, ambient=ambient)
    arguments = dict(device=_device(model="Photo Research " + model, serial="PR-SOFTWARE"),
                     operation_id="software-op", sample_id="P1",
                     mode="ambient_spot" if ambient else "emissive_spot",
                     acquired_at="2026-10-14T00:00:00Z", provenance="simulated",
                     raw_backend_output="{}")
    observation = observation_from_native_capture(facts, **arguments)
    assert observation.backend_reported.xyz is None
    assert observation.backend_reported.spectrum.unit == "unverified native spectral density units"
    capture = facts["capture"]
    capture.update(spectrum=None, spectrum_parsed=False, spectrum_complete=False,
                   xyz=[0, 0, 0], xyz_complete=True, xyz_parsed=True)
    observation = observation_from_native_capture(facts, **arguments)
    assert observation.backend_reported.xyz.values == (0, 0, 0)
    assert observation.backend_reported.xyz.unit == "unverified native XYZ units"
    assert observation.backend_reported.spectrum is None
    capture["xyz_parsed"] = False
    with pytest.raises(ProtocolError, match="native XYZ is malformed"):
        observation_from_native_capture(facts, **arguments)
    # A failure before any quantity was parsed creates no attributed observation.
    capture["xyz"] = None
    facts["identity"]["verified"] = False
    assert observation_from_native_capture(facts, **arguments) is None


# -- settings decoder bounds ----------------------------------------------------------

CYCLES, PRIMARY, APERTURE = 1 << 1, 1 << 4, 1 << 8


def applied_report(*, failed=False, blocked=0, requested=None, values=None):
    """One PR report for dispatched changes, with the native masks it actually has."""
    events = pr_control_events()
    requested = dict(cycles=3) if requested is None else requested
    bits = NativeControls.from_changes("pr655", requested).present
    events[0].update(
        status_code=0x140003 if failed else 0, failed=failed, changed=bits, attempted=bits,
        acknowledged=0 if failed else bits,
        confirmed=0 if failed else bits & ~ack_only_mask("pr655"),
        blocked=bits | blocked if failed else blocked,
        requested=control_values("pr655", **requested),
        before=control_values("pr655", cycles=1, primaryAccessoryId=1, apertureId=1),
        values=control_values("pr655", **(values or dict(
            dict(cycles=1, primaryAccessoryId=1, apertureId=1),
            **({} if failed else {key: value for key, value in requested.items()
                                  if key not in ACK_ONLY_FIELDS["pr655"]})))),
    )
    return events


def test_successful_report_must_confirm_every_requested_field():
    events = applied_report(requested=dict(cycles=3, speedModeCode=0))
    native_control_facts(events)  # SG is acknowledged-only; cycles is confirmed.
    events[0]["confirmed"] = 0
    with pytest.raises(ProtocolError, match="lack confirmation of the complete request"):
        native_control_facts(events)


def test_failed_report_must_block_every_attempted_field():
    events = applied_report(failed=True)
    assert native_control_facts(events)["blocked"] == CYCLES
    events[0]["blocked"] = 0
    with pytest.raises(ProtocolError, match="do not block every attempted field"):
        native_control_facts(events)


def test_inventory_ids_and_control_values_stop_at_exact_binary64_integers():
    events = pr_control_events()
    events[0].update(accessory_count=1, reported_accessory_count=1, accessory_list_valid=True)
    row = dict(event="native_accessory", driver="pr655", accessory_index=0,
               accessory_count=1, id=2**53 - 1, name="n", type="Primary",
               photometry="Luminance", radiometry="Radiance")
    assert native_control_facts([*events, row])["accessories"][0]["id"] == 2**53 - 1
    with pytest.raises(ProtocolError, match="inventory is malformed"):
        native_control_facts([*events, dict(row, id=2**53)])
    events[0]["values"] = control_values("pr655", addon1Id=2**53 - 1)
    assert native_control_facts([*events, row])["values"]["addon1Id"] == 2**53 - 1
    events[0]["values"] = control_values("pr655", addon1Id=2**53)
    with pytest.raises(ProtocolError, match="not exactly representable"):
        native_control_facts([*events, row])


def valid_pr_setup(**changes):
    return dict(pr_control_events()[0]["setup"], **changes)


@pytest.mark.parametrize("model,changes", [
    (None, dict(primary=-1)), (None, dict(primary=2**53)), (None, dict(aperture=-1)),
    (None, dict(aperture=2**53)), (None, dict(addons=[-2, -1, -1])),
    (None, dict(addons=[2**53, -1, -1])), (None, dict(units=2)), (None, dict(cycles=0)),
    (None, dict(cycles=100)), (None, dict(observer=3)), (None, dict(dark_mode=2)),
    (None, dict(sync_mode=2)), (None, dict(gain=2**31)), (None, dict(capture_mode=-(2**31) - 1)),
    (None, dict(exposure_ms=2, adaptive=False)), (None, dict(exposure_ms=30001, adaptive=False)),
    (None, dict(exposure_ms=-1, adaptive=False)), (None, dict(sync_value=-1.0)),
    (None, dict(frequency_hz=-1.0)), (None, dict(exposure_ms=100)),
    (None, dict(adaptive_known=False)), (None, dict(sensitivity_known=False, extended=True)),
    (None, dict(exposure_ms=7000, adaptive=False)),
    ("PR-655", dict(exposure_ms=6001, adaptive=False, extended=True)),
    ("PR-655", dict(exposure_ms=100, adaptive=False, extended=True)),
    ("PR-670", dict(exposure_ms=5, adaptive=False)),
])
def test_pr_setup_admits_only_the_parsed_d601_d602_bounds(model, changes):
    from color_measurement.instrument import _pr_control_setup

    with pytest.raises(ProtocolError, match="parsed D601/D602 bounds"):
        _pr_control_setup(valid_pr_setup(**changes), True, model=model)


@pytest.mark.parametrize("model,changes", [
    (None, {}), (None, dict(primary=2**53 - 1, aperture=0, addons=[-1, 2**53 - 1, 0])),
    (None, dict(exposure_ms=3, adaptive=False)), ("PR-655", dict(exposure_ms=3, adaptive=False)),
    ("PR-670", dict(exposure_ms=30000, adaptive=False, extended=True)),
    (None, dict(sync_value=-0.0, adaptive_known=False, adaptive=False, exposure_ms=50)),
])
def test_pr_setup_keeps_every_parsed_value(model, changes):
    from color_measurement.instrument import _pr_control_setup

    setup = valid_pr_setup(**changes)
    assert _pr_control_setup(setup, True, model=model) is setup


@pytest.mark.parametrize("phase", ["setup_before", "setup_after"])
def test_pr_capture_setups_share_the_settings_setup_bounds(phase):
    events, _ = pr_raw_capture("PR-655")
    capture = events[0]["capture"]
    capture.update(setup_before=valid_pr_setup(), setup_after=valid_pr_setup())
    assert native_facts(events)["capture"][phase]["units"] == 1
    capture[phase] = valid_pr_setup(exposure_ms=6001, adaptive=False, extended=True,
                                     sensitivity_known=True)
    with pytest.raises(ProtocolError, match="parsed D601/D602 bounds"):
        native_facts(events)
    capture[phase] = valid_pr_setup(units=7)
    with pytest.raises(ProtocolError, match="parsed D601/D602 bounds"):
        native_facts(events)


# -- refused exchanges keep their settings debt ----------------------------------------


def finished(operation):
    operation.dispatch()
    deadline = time.monotonic() + 3
    while not operation.done:
        assert time.monotonic() < deadline
        time.sleep(0.005)
    return json.loads(operation.result_json())


@pytest.mark.parametrize("case", ["malformed", "mismatch"])
def test_refused_settings_exchange_keeps_uncertain_debt_and_its_original_events(
    monkeypatch, case,
):
    events = applied_report()
    if case == "malformed":
        # The worker wrote SN3, but its report fails the shared decoder.
        events[0]["setup"] = dict(events[0]["setup"], descriptions=["bad\u00e9"] * 15)
    else:
        events[0]["requested"] = control_values("pr655", cycles=4)
        events[0]["values"] = control_values("pr655", cycles=4)
    scripted_session(monkeypatch, events)
    front = native_frontend()
    result = finished(front.prepare(controls_request(front, changes=dict(cycles=3)),
                                    timeout=30))
    configuration = dict(result["configuration"])
    assert result["status"] == "failed"
    assert ("Malformed native PR setup" if case == "malformed"
            else "differs from dispatched changes") in result["error"]
    assert front._control_pending() == CYCLES
    assert "native_control_json" not in configuration
    retained = json.loads(configuration["native_control_exchange_json"])
    assert [event["event"] for event in retained][:2] == ["connected", "native_control"]


def test_failed_report_cannot_complete_and_keeps_its_declared_debt(monkeypatch):
    events = applied_report(failed=True, blocked=APERTURE)
    scripted_session(monkeypatch, events)  # Completed terminal without an error event.
    front = native_frontend()
    result = finished(front.prepare(controls_request(front, changes=dict(cycles=3)),
                                    timeout=30))
    assert result["status"] == "failed"
    assert "The helper completed after failed native controls" in result["error"]
    assert front._control_pending() == CYCLES | APERTURE
    retained = json.loads(dict(result["configuration"])["native_control_json"])
    assert retained["failed"] is True and retained["blocked"] == CYCLES | APERTURE


def test_refused_spot_completion_keeps_collateral_and_dispatched_debt(monkeypatch):
    # pr655_control.c:478-483: SP moved the aperture; the report succeeds with
    # aperture debt. The worker then claims completion without its reading.
    events = applied_report(requested=dict(primaryAccessoryId=2), blocked=APERTURE,
                            values=dict(cycles=1, primaryAccessoryId=2, apertureId=3))
    scripted_session(monkeypatch, events)
    front = native_frontend()
    result = finished(front.prepare(
        controls_request(front, workflow="spot", changes=dict(primaryAccessoryId=2)),
        timeout=400))
    assert result["status"] == "failed"
    assert "completed without the requested results" in result["error"]
    assert front._control_pending() == PRIMARY | APERTURE
    assert json.loads(dict(result["configuration"])["native_control_json"])["blocked"] == APERTURE
    with pytest.raises(ValueError, match="every unresolved field"):
        front.prepare(controls_request(front, workflow="spot",
                                       changes=dict(primaryAccessoryId=2)), timeout=400)


def test_cleanup_failure_after_validated_settings_keeps_collateral_debt(monkeypatch):
    events = applied_report(requested=dict(primaryAccessoryId=2), blocked=APERTURE,
                            values=dict(cycles=1, primaryAccessoryId=2, apertureId=3))
    session = scripted_session(monkeypatch, events)

    def cleanup_error():
        raise OSError("Owned cleanup failed after the validated control report")

    monkeypatch.setattr(session, "close", cleanup_error)
    front = native_frontend()
    result = finished(front.prepare(controls_request(front, changes=dict(primaryAccessoryId=2)),
                                    timeout=30))
    assert result["status"] == "failed"
    assert front._control_pending() == PRIMARY | APERTURE
    assert json.loads(dict(result["configuration"])["native_control_json"])["blocked"] == APERTURE


def test_refused_report_keeps_the_native_failure_stage_and_code(monkeypatch):
    events = applied_report(failed=True)
    events[0]["setup"] = dict(events[0]["setup"], descriptions=["bad\u00e9"] * 15)
    scripted_session(monkeypatch, [*events, dict(
        event="error", stage="native_controls", native_code=0x140003,
    )], failed=True)
    front = native_frontend()
    result = finished(front.prepare(controls_request(front, changes=dict(cycles=3)),
                                    timeout=30))
    assert result["status"] == "failed"
    assert (result["error_stage"], result["error_native_code"]) == ("native_controls", 0x140003)
    assert "Malformed native PR setup" in result["error"]
    assert front._control_pending() == CYCLES
    assert "native_control_exchange_json" in dict(result["configuration"])


def test_successful_settings_report_keeps_collateral_aperture_debt(monkeypatch):
    events = applied_report(requested=dict(primaryAccessoryId=2), blocked=APERTURE,
                            values=dict(cycles=1, primaryAccessoryId=2, apertureId=3))
    scripted_session(monkeypatch, events)
    front = native_frontend()
    result = finished(front.prepare(controls_request(front, changes=dict(primaryAccessoryId=2)),
                                    timeout=30))
    assert result["status"] == "completed", result["error"]
    assert front._control_pending() == APERTURE
    with pytest.raises(ValueError, match="every unresolved field"):
        front.prepare(controls_request(front, workflow="spot",
                                       changes=dict(primaryAccessoryId=2)), timeout=400)
    recovery = applied_report(requested=dict(apertureId=1),
                              values=dict(cycles=1, primaryAccessoryId=2, apertureId=1))
    scripted_session(monkeypatch, recovery)
    result = finished(front.prepare(controls_request(front, changes=dict(apertureId=1)),
                                    timeout=30))
    assert result["status"] == "completed", result["error"]
    assert front._control_pending() == 0


@pytest.mark.parametrize("meter_serial", [None, "OTHER-UNIT"])
def test_native_controls_require_a_worker_pinned_to_the_bound_serial(meter_serial):
    with pytest.raises(ValueError, match="pinned to the bound serial"):
        native_frontend(meter_serial=meter_serial)
    assert native_frontend(meter_serial=meter_serial, supported=False)._control_key == (
        "Photo Research PR-670", "PR-SOFTWARE")
