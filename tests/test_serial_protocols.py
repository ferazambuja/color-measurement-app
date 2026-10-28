"""Synthetic protocol inputs, native quantities and refusals; no ports or hardware."""

import math

import pytest

from color_measurement import serial_protocols as protocol
from color_measurement.serial_transport import SerialProtocolError


@pytest.mark.parametrize("payload", ["1_000", "١", "nan", "inf", "1e309", "1e-999",
                                    "-1e-999", "", "1,,2", "0x10", "1/2", "1\n2"])
def test_all_direct_protocols_share_exact_finite_ascii_decimal_refusal(payload):
    with pytest.raises(SerialProtocolError):
        protocol.numbers(payload)


def test_direct_decimal_parser_preserves_signed_bins_subnormals_and_literal_zero():
    values = protocol.numbers("-2,5e-324,-0.0,0e-999,1.23E+2", 5)
    assert values == (-2, float.fromhex("0x0.0000000000001p-1022"), 0, 0, 123)
    assert math.copysign(1, values[2]) == -1


@pytest.mark.parametrize("payload,kind", [
    ("-1 msec", "milliseconds"), ("1 ms", "milliseconds"), ("nan Hz", "hertz"),
    ("1.00000000000000001", "integer"), ("0", "positiveInteger"),
    ("unknown", "syncMode"), ("", "text"),
])
def test_cr_capture_metadata_refuses_bad_units_modes_and_original_fractional_counts(payload, kind):
    with pytest.raises(SerialProtocolError):
        protocol.cr_capture_value(payload, kind)


@pytest.mark.parametrize("line,command", [("OK:0:RM XYZ:1,2,3", "RM XYZ"),
                                         ("OK:1:M:warning preserved", "M")])
def test_cr_reply_preserves_native_success_status_and_matches_command(line, command):
    payload, code = protocol.cr_reply(line, command)
    assert payload == line.split(":", 3)[3] and code == int(line.split(":")[1])
    assert protocol.cr_reply("OK:0:ExposureMode:", "SM ExposureMode 1",
                             expected_label="ExposureMode") == ("", 0)


@pytest.mark.parametrize("line", ["OK:-1:M:bad", "ER:0:M:bad", "OK:0:RM XYZ:1",
                                 "OK:0:M", "ER:-302:wrong:bad", "OK:1.5:M:bad"])
def test_cr_invalid_or_mismatched_framing_is_not_a_complete_error(line):
    with pytest.raises(SerialProtocolError) as caught:
        protocol.cr_reply(line, "M")
    assert not isinstance(caught.value, protocol.SerialInstrumentError)


@pytest.mark.parametrize("line", ["ER:-302:M:exposure", "ER:-500:Unsupported:command"])
def test_cr_complete_error_keeps_code_and_original_general_error_label(line):
    with pytest.raises(protocol.SerialInstrumentError) as caught:
        protocol.cr_reply(line, "M")
    assert caught.value.raw == line and caught.value.code == int(line.split(":")[1])


@pytest.mark.parametrize("kind,quantity,total_unit", [(0, "radiance", "W/(m^2 sr)"),
    (1, "irradiance", "W/m^2"), (2, "radiant_intensity", "W/sr"), (3, "radiant_flux", "W")])
def test_cr_uses_returned_spacing_and_keeps_total_units_separate_from_spectral_density(
    kind, quantity, total_unit,
):
    spectrum, facts = protocol.cr_spectrum("380,780,4,101", ["-0.1"] * 101, f"{kind},1,2")
    assert spectrum.wavelengths_nm == tuple(range(380, 781, 4))
    assert spectrum.values == (-0.1,) * 101 and spectrum.quantity == quantity
    assert spectrum.unit == "unverified native spectral density units"
    assert facts["radiometric_unit"] == total_unit
    assert not facts["spectral_density_units_confirmed"]


@pytest.mark.parametrize("header", ["380,780,4,100", "380,780,0,101", "380,780,-4,101",
                                   "780,380,4,101", "380,780,4,101.5", "0,400,4,101",
                                   "380,780,0.001,400001"])
def test_cr_inconsistent_or_unbounded_returned_grid_refuses(header):
    with pytest.raises(SerialProtocolError):
        protocol.cr_grid(header)


@pytest.mark.parametrize("code", ["ER00", "ER02", "ER17", "ER30"])
def test_cs_complete_error_is_retained_without_a_busy_or_idle_inference(code):
    with pytest.raises(protocol.SerialInstrumentError) as caught:
        protocol.cs_reply(code, "MEAS,1")
    assert caught.value.code == code and caught.value.raw == code


@pytest.mark.parametrize("line", ["ER02,data", "ER00,", "OK01", "ER2", "OK00,123\x00"])
def test_cs_malformed_response_is_not_a_complete_device_error(line):
    with pytest.raises(SerialProtocolError) as caught:
        protocol.cs_reply(line, "MEAS,1")
    assert not isinstance(caught.value, protocol.SerialInstrumentError)


def test_cs_acknowledgement_and_two_phase_measurement_duration_stay_distinct():
    assert protocol.cs_reply("OK00", "RMTS,1", acknowledgement=True) == ""
    with pytest.raises(SerialProtocolError):
        protocol.cs_reply("OK00,", "RMTS,1", acknowledgement=True)
    assert protocol.cs_duration(protocol.cs_reply("OK00,002", "MEAS,1")) == 2
    assert protocol.cs_duration("242") == 242


@pytest.mark.parametrize("duration", ["0.002", "02", "001", "243", "+02", "002 "])
def test_cs_announced_duration_is_three_digits_in_integer_seconds(duration):
    with pytest.raises(SerialProtocolError):
        protocol.cs_duration(duration)


@pytest.mark.parametrize("model,variation", [("CS-2000", 1), ("CS-2000A", 2)])
def test_cs_identity_is_the_explicit_model_variation_and_seven_digit_serial(model, variation):
    assert protocol.cs_identity(f"{model},{variation},0123456", model) == dict(
        model=model, variation=variation, serial="0123456",
    )
    with pytest.raises(SerialProtocolError):
        protocol.cs_identity(f"{model},3,0123456", model)
    with pytest.raises(SerialProtocolError):
        protocol.cs_identity(f"{model},{variation},123456", model)


def test_cs_native_conditions_preserve_microseconds_and_no_invented_observer_field():
    values = protocol.cs_conditions("3,1,123456,1,0,2,1,10")
    assert values["integration_time_microseconds"] == 123456
    assert values["external_nd_filter"] == 2 and values["measurement_angle"] == 1
    assert "observer" not in values and "integration_time_seconds" not in values
    with pytest.raises(SerialProtocolError):
        protocol.cs_conditions("3,1,0,1,0,2,1,10")


def test_cs_spectrum_uses_all_four_native_blocks_and_refuses_calculation_error_sentinel():
    blocks = [",".join(["-0.25"] * count) for count in (100, 100, 100, 101)]
    spectrum = protocol.cs_spectrum(blocks)
    assert spectrum.wavelengths_nm == tuple(range(380, 781))
    assert spectrum.values == (-0.25,) * 401
    assert spectrum.quantity == "spectral_radiance" and spectrum.unit == "W/(m^2 sr nm)"
    blocks[-1] = blocks[-1].replace("-0.25", "-9.9999e9", 1)
    with pytest.raises(SerialProtocolError, match="sentinel"):
        protocol.cs_spectrum(blocks)


@pytest.mark.parametrize("kind", ["cr-count", "cr-quantity", "cs-condition"])
def test_native_integer_semantics_refuse_fractional_tokens_before_binary64_rounding(kind):
    with pytest.raises(SerialProtocolError):
        if kind == "cr-count":
            protocol.cr_grid("380,780,4,101.0000000000000000001")
        elif kind == "cr-quantity":
            protocol.cr_spectrum("380,780,4,101", ["1"] * 101, "0.9999999999999999999,1,2")
        else:
            protocol.cs_conditions("3,1,123456.0000000000000000001,1,0,2,1,10")
