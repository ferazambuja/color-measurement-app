"""The two authenticated substituted C writers must agree on a serial pin."""

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import authenticate_worker, supplied_stub

from color_measurement.argyll_description import NativeDescriptionClient
from color_measurement.discovery import InstrumentBuild
from color_measurement.instrument import Instrument


def _workers():
    session = Path(supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB"))
    scope = json.loads(session.with_name("report.json").read_text()).get("driver_scope")
    if scope in ("i1pro3", "i1pro"):
        pytest.skip("narrow build has no description worker")
    description = session.with_name("description-stub-worker" + session.suffix)
    authenticate_worker(description)
    return session, description


def test_valid_utf8_serial_is_identical_in_description_binding_and_reading():
    session, description = _workers()
    client = NativeDescriptionClient.for_offline_tests(description, "utf8_serial")
    native = json.loads(client.list_candidates_json())["data"]["candidates"][0]
    catalog = json.dumps({"instrument_types": [{
        "enum": "instI1Disp3", "native_type": native["dtype"],
        "source_label": "i1 DisplayPro",
        "initialization_assessed": native["initialization_assessed"],
        "initialization_refusal": native["initialization_refusal"],
    }]})
    digest = hashlib.sha256(session.read_bytes()).hexdigest()
    build = InstrumentBuild(client, session, expected_helper_sha256=digest)
    found = build.attached(catalog_json=catalog)[0]
    described = build.describe(found, accept_disclosed_initialization=True,
                               catalog_json=catalog)
    assert described.serial == "CM-ñ-1"
    bound = build.bind(described, catalog_json=catalog)
    assert bound._meter.expected_serial == described.serial
    meter = replace(bound._meter, environment={**os.environ,
                                               "CM_COLORIMETER_FIXTURE": "utf8_serial"})
    result = meter.measure("emissive_spot", spectral=False)
    assert (result.serial, result.serial_encoding) == (described.serial, "utf8")
    connected = next(event for event in result.events if event["event"] == "connected")
    assert (connected["serial"], connected["serial_encoding"]) == (described.serial, "utf8")


def test_invalid_driver_serial_bytes_keep_their_lossless_json_fallback():
    session = Path(supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB"))
    digest = hashlib.sha256(session.read_bytes()).hexdigest()
    meter = Instrument(session, "instI1Disp3",
                       expected_helper_sha256=digest,
                       environment={**os.environ,
                                    "CM_COLORIMETER_FIXTURE": "invalid_serial_bytes"})
    result = meter.measure("emissive_spot", spectral=False)
    assert (result.serial, result.serial_encoding) == ("\u00ed\u00a0\u0080", "bytes")
    connected = next(event for event in result.events if event["event"] == "connected")
    assert (connected["serial"], connected["serial_encoding"]) == (
        result.serial, result.serial_encoding)
