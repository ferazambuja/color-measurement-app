"""Complete settings targets and exact native plans; no serial writes or hardware."""

import pytest

from color_measurement.serial_settings import (
    cr_setting_list,
    cr_setting_value,
    cr_settings_plan,
    cr_settings_specs,
    cs_settings_plan,
    cs_speed_values,
    cs_sync_values,
)
from color_measurement.serial_transport import SerialProtocolError


def commands(plan):
    return [item["command"] for item in plan["commands"]]


def cr_report(model="CR-100"):
    choices = dict(mode=[(6, "Temporal"), (9, "Colorimeter")],
                   exposureMode=[(2, "Auto"), (7, "Fixed")],
                   rangeMode=[(3, "Auto"), (8, "Fixed")], range=[(4, "C"), (11, "D")],
                   syncMode=[(2, "None"), (5, "Manual")], speed=[(7, "Slow"), (9, "Fast")],
                   accessory=[(4, "Lens"), (8, "Other lens")],
                   userCalibrationMode=[(3, "None"), (6, "Matrix"), (8, "Match")],
                   matrixId=[(5, "Matrix one")], matchId=[(7, "Match one")])
    controls = {}
    for key, spec in cr_settings_specs(model).items():
        rows = choices.get(key, [(4, "ND"), (9, "Other ND")])
        controls[key] = dict(available=key != "mode",
                             choices=[dict(value=value, label=label) for value, label in rows],
                             minimum=1, maximum=1000)
    values = dict(mode=6, exposureMode=7, exposureMs=20, rangeMode=8, range=11,
                  exposureMultiplier=3, syncMode=2, syncFrequencyHz=60, speed=7,
                  accessory=4, filter1=4, filter2=4, filter3=4, userCalibrationMode=3,
                  matrixId=5, matchId=7)
    return dict(values={key: value for key, value in values.items() if key in controls},
                controls=controls)


def test_cr_counted_lists_preserve_native_ids_labels_and_third_column_without_clear_code():
    assert cr_setting_list("2", ["4,Lens,0", "9,Other lens,1"], columns=3) == [
        dict(value=4, label="Lens", quantity_type="0"),
        dict(value=9, label="Other lens", quantity_type="1"),
    ]
    assert cr_setting_list("None", [], columns=2) == []
    assert cr_setting_list("0", [], columns=3) == []
    assert cr_setting_list("1e0", ["5.000,ND"], columns=2)[0]["value"] == 5
    assert cr_setting_list(" 1 ", ["5.000,ND"], columns=2)[0]["value"] == 5
    with pytest.raises(SerialProtocolError):
        cr_setting_list(" 1.000000000000000001 ", ["5,ND"], columns=2)


@pytest.mark.parametrize("count,rows,columns", [
    ("2", ["4,Lens"], 2), ("1", ["4,Lens", "9,ND"], 2),
    ("None", ["4,Lens"], 2), ("257", [], 2), ("1.00000000000000001", ["4,Lens"], 2),
    ("1", ["9007199254740990.5,Lens"], 2), ("1", ["-1,Lens"], 2),
    ("2", ["4,Lens", "4,Other"], 2), ("1", ["4,"], 2),
    ("1", ["4,Lens,0"], 2), ("1", ["4,Lens\t"], 2),
])
def test_cr_configuration_rows_refuse_malformed_count_ids_and_incomplete_lists(
    count, rows, columns,
):
    with pytest.raises(SerialProtocolError):
        cr_setting_list(count, rows, columns=columns)


def test_cr_readback_uses_names_and_unit_suffixes_without_inventing_current_maximum_exposure():
    controls = cr_report()["controls"]
    assert cr_setting_value("Manual", "exposureMode", controls["exposureMode"]) == 7
    assert cr_setting_value("ND,Other ND,ND", "filter2", controls["filter2"]) == 9
    assert cr_setting_value("5e0", "matrixId", controls["matrixId"]) == 5
    assert cr_setting_value("12.5 msec", "exposureMs", controls["exposureMs"]) == 12.5
    assert cr_setting_value("60 Hz", "syncFrequencyHz", controls["syncFrequencyHz"]) == 60
    assert cr_setting_value("NA", "exposureMs", controls["exposureMs"]) is None
    assert cr_setting_value("1 degree", "aperture", {}) == "1 degree"
    with pytest.raises(ValueError, match="no documented current"):
        cr_setting_value("20 msec", "maxAutoExposureMs", {})
    for key, raw in [("matrixId", "5.000000000000000001"), ("exposureMs", "20"),
                     ("exposureMultiplier", "0"), ("exposureMultiplier", "1.5"),
                     ("filter1", "ND,ND"), ("syncFrequencyHz", "60 hertz"),
                     ("matrixId", "9"), ("exposureMode", "Unknown")]:
        with pytest.raises(SerialProtocolError):
            cr_setting_value(raw, key, controls[key])


@pytest.mark.parametrize("model", ["CR-100", "CR-250", "CR-300"])
def test_cr_automatic_recipe_uses_actual_ids_and_leaves_unrequested_settings(model):
    before = cr_report(model)
    plan = cr_settings_plan(model, {}, before, automatic=True)
    assert commands(plan) == (["SM Mode 9", "SM ExposureMode 2", "SM RangeMode 3"]
                              if model == "CR-100" else ["SM ExposureMode 2"])
    assert plan["target"]["exposureMultiplier"] == 3
    assert plan["target"]["accessory"] == 4
    assert plan["target"]["syncMode"] == 2
    assert "maxAutoExposureMs" not in plan["target"]
    before["values"].update(plan["requested"])
    assert commands(cr_settings_plan(model, {}, before, automatic=True)) == []


@pytest.mark.parametrize("changes,bound", [
    (dict(exposureMode=7), "exposureMs"),
    (dict(rangeMode=8), "exposureMs"),
    (dict(syncMode=5), "syncFrequencyHz"),
])
def test_cr_manual_mode_change_cannot_bypass_unavailable_numeric_dependency(changes, bound):
    before = cr_report()
    before["controls"][bound].update(available=False, minimum=None, maximum=None)
    with pytest.raises(ValueError, match=bound):
        cr_settings_plan("CR-100", changes, before)
    before["controls"][bound].update(available=True, minimum=1, maximum=1000)
    before["values"][bound] = None
    with pytest.raises(ValueError, match=bound):
        cr_settings_plan("CR-100", changes, before)
    assert commands(cr_settings_plan("CR-100", {}, before, automatic=True)) == [
        "SM Mode 9", "SM ExposureMode 2", "SM RangeMode 3",
    ]


@pytest.mark.parametrize("current,changes,bound,command", [
    (dict(exposureMode=2, exposureMs=5000), dict(exposureMode=7), "exposureMs",
     "SM ExposureMode 7"),
    (dict(syncFrequencyHz=2000), dict(syncMode=5), "syncFrequencyHz", "SM SyncMode 5"),
])
def test_cr_manual_mode_change_refuses_current_dependency_outside_reported_bounds(
    current, changes, bound, command,
):
    # Only the mode is requested; the unrequested current readback would govern
    # the exposure or synchronization and lies outside the reported 1..1000 bounds.
    before = cr_report()
    before["values"].update(current)
    with pytest.raises(ValueError, match=f"requires {bound} within reported bounds"):
        cr_settings_plan("CR-100", changes, before)
    before["values"][bound] = 1000
    assert commands(cr_settings_plan("CR-100", changes, before)) == [command]


@pytest.mark.parametrize("key,value,current", [
    ("exposureMultiplier", 5, {}),
    ("maxAutoExposureMs", 500, dict(exposureMode=2)),
])
def test_cr_plan_refuses_a_zero_reported_lower_bound(key, value, current):
    # Neither control has a manual-mode dependency check, so only the general
    # bound rule can refuse a nonpositive native minimum.
    before = cr_report()
    before["values"].update(current)
    before["controls"][key]["minimum"] = 0
    with pytest.raises(ValueError, match=f"CR {key} is outside reported choices or bounds"):
        cr_settings_plan("CR-100", {key: value}, before)
    before["controls"][key]["minimum"] = 1
    assert cr_settings_plan("CR-100", {key: value}, before)["requested"][key] == value


def test_cr_fixed_range_dependencies_and_native_setter_order_are_model_scoped():
    before = cr_report()
    before["values"].update(exposureMode=2, rangeMode=3, range=4)
    changes = dict(exposureMode=7, rangeMode=8, range=11, exposureMs=30)
    assert commands(cr_settings_plan("CR-100", changes, before)) == [
        "SM RangeMode 8", "SM Range 11", "SM Exposure 30", "SM ExposureMode 7",
    ]
    for changes in [dict(exposureMode=7), dict(exposureMs=30), dict(range=11)]:
        with pytest.raises(ValueError):
            cr_settings_plan("CR-100", changes, before)
    assert commands(cr_settings_plan("CR-250", dict(exposureMs=30), cr_report("CR-250"))) == [
        "SM Exposure 30",
    ]


def test_cr_correction_disable_precedes_accessory_and_profiles_precede_enable():
    before = cr_report()
    before["values"]["userCalibrationMode"] = 6
    with pytest.raises(ValueError, match="separate accessory"):
        cr_settings_plan("CR-100", dict(accessory=8), before)
    changed = cr_settings_plan("CR-100", dict(accessory=8, userCalibrationMode=3), before)
    assert commands(changed) == [
        "SM UserCalibMode 3", "SM Accessory 8",
    ]
    before["values"].update(userCalibrationMode=3, matchId=None)
    assert commands(cr_settings_plan("CR-100", dict(matchId=7, userCalibrationMode=8), before)) == [
        "SM Match 7", "SM UserCalibMode 8",
    ]
    with pytest.raises(ValueError, match="reported matchId"):
        cr_settings_plan("CR-100", dict(userCalibrationMode=8), before)


def test_cr_acknowledgement_only_auto_limit_is_never_skipped_as_readback():
    before = cr_report("CR-300")
    before["values"]["exposureMode"] = 2
    assert commands(cr_settings_plan("CR-300", dict(maxAutoExposureMs=100), before)) == [
        "SM MaxAutoExposure 100",
    ]
    before["values"]["maxAutoExposureMs"] = 100  # An acknowledgement cannot supply RS readback.
    assert commands(cr_settings_plan("CR-300", dict(maxAutoExposureMs=100), before)) == [
        "SM MaxAutoExposure 100",
    ]
    before["values"]["exposureMode"] = 7
    with pytest.raises(ValueError, match="requires Auto"):
        cr_settings_plan("CR-300", dict(maxAutoExposureMs=100), before)


@pytest.mark.parametrize("changes", [dict(mode=9), dict(aperture=1), dict(speed=7),
                                   dict(filter1=0), dict(exposureMultiplier=1.5),
                                   dict(syncFrequencyHz=60), dict(matrixId=5),
                                   dict(exposureMs=True), dict(exposureMs=float("inf"))])
def test_cr_unsupported_and_dependent_settings_refuse_before_commands(changes):
    with pytest.raises(ValueError):
        cr_settings_plan("CR-100", changes, cr_report())


def test_cr_automatic_policy_requires_unambiguous_reported_colorimeter_and_availability():
    before = cr_report()
    before["controls"]["mode"]["choices"].append(dict(value=10, label="Colorimeter"))
    with pytest.raises(ValueError, match="one supported"):
        cr_settings_plan("CR-100", {}, before, automatic=True)
    before = cr_report()
    before["controls"]["mode"]["choices"] = []
    with pytest.raises(ValueError):
        cr_settings_plan("CR-100", {}, before, automatic=True)
    before = cr_report()
    before["controls"]["exposureMode"]["available"] = False
    with pytest.raises(ValueError):
        cr_settings_plan("CR-100", dict(exposureMode=2), before)
    assert cr_settings_specs("CR-100")["mode"]["since"] == (1, 16)
    assert cr_settings_specs("CR-300")["speed"]["since"] == (1, 17)
    with pytest.raises(ValueError, match="colorimeter-only"):
        cr_settings_plan("CR-300", dict(userCalibrationMode=6), cr_report("CR-300"))
    before = cr_report()
    before["controls"]["syncFrequencyHz"]["minimum"] = 1
    valid = dict(syncMode=5, syncFrequencyHz=60)
    assert cr_settings_plan("CR-100", valid, before)["target"]["syncFrequencyHz"] == 60
    for low, high in ((61, 100), (0, 59), (0, 100), (-1, 100), (100, 1), (0, float("inf"))):
        before["controls"]["syncFrequencyHz"].update(minimum=low, maximum=high)
        with pytest.raises(ValueError):
            cr_settings_plan("CR-100", valid, before)


def cs_report():
    return dict(values=dict(speedMode=3, integrationTimeSeconds=0.1, internalNDMode=0,
                            syncMode=1, frequencyHz=60, closeupLens=0, externalNDFilter=1,
                            userCalibrationChannel=0), speedLayout="modern")


@pytest.mark.parametrize("payload,variation,layout,interval,nd", [
    ("0", 1, "older-hardware", None, 2), ("2,1", 1, "older-hardware", 1, 2),
    ("0,1", 1, "modern", None, 1), ("2,16,2", 2, "modern", 16, 2),
    ("3,100000,0", 1, "unknown", 0.1, 0), ("3,100000,1", 2, "modern", 0.1, 1),
    ("4,1,2", 1, "modern", 1, 2),
])
def test_cs_readback_preserves_actual_hardware_layout_and_native_restore_command(
    payload, variation, layout, interval, nd,
):
    values = cs_speed_values(payload, variation=variation)
    assert values["speedLayout"] == layout and values["integrationTimeSeconds"] == interval
    assert values["internalNDMode"] == nd and values["restoreCommand"] == "SPMS," + payload


@pytest.mark.parametrize("payload,variation", [("0", 2), ("2,1", 2), ("4,1", 1),
    ("3,5000,2", 2), ("3,4999,0", 1), ("2,17,2", 2), ("3,100000.0000000000000001,0", 1)])
def test_cs_invalid_or_wrong_model_speed_layout_refuses_without_a_firmware_guess(
    payload, variation,
):
    with pytest.raises((ValueError, SerialProtocolError)):
        cs_speed_values(payload, variation=variation)


@pytest.mark.parametrize("payload,mode,frequency", [("0", 0, None), ("2", 2, None),
                                                 ("1,2000", 1, 20), ("1,6025", 1, 60.25)])
def test_cs_sync_centihertz_is_separate_from_measured_refresh_frequency(payload, mode, frequency):
    assert cs_sync_values(payload) == dict(syncMode=mode, frequencyHz=frequency)


def test_cs_normal_recipe_leaves_accessory_and_correction_choices_unchanged():
    before = cs_report()
    plan = cs_settings_plan({}, before, automatic=True)
    assert commands(plan) == ["SPMS,2,1,2", "SCMS,0"]
    assert plan["target"]["externalNDFilter"] == 1
    assert before["values"]["speedMode"] == 3 and before["values"]["integrationTimeSeconds"] == 0.1
    assert not plan["preflight"]


def test_cs_manual_override_never_borrows_the_automatic_recipe_interval_or_nd():
    before = cs_report()
    before["values"].update(speedMode=2, integrationTimeSeconds=1, internalNDMode=2)
    with pytest.raises(ValueError):
        cs_settings_plan(dict(speedMode=3), before, automatic=True)
    plan = cs_settings_plan(dict(speedMode=3, integrationTimeSeconds=0.1, internalNDMode=1),
                            before, automatic=True)
    assert commands(plan) == ["SPMS,3,000100000,1", "SCMS,0"]


def test_cs_accessory_replacement_disables_incompatible_compensation_before_enabling():
    plan = cs_settings_plan(dict(closeupLens=1, externalNDFilter=0), cs_report())
    assert commands(plan) == ["NDFS,0", "LNSS,1"]
    with pytest.raises(ValueError):
        cs_settings_plan(dict(closeupLens=1), cs_report())


def test_cs_channel_change_requires_native_name_preflight_before_any_setter():
    plan = cs_settings_plan(dict(userCalibrationChannel=3), cs_report())
    assert plan["preflight"] == [dict(command="UCPR,2,03", channel=3)]
    assert commands(plan) == ["UCCS,03"]


@pytest.mark.parametrize("changes", [dict(speedMode=4), dict(speedMode=2, internalNDMode=1)])
def test_cs_manual_readback_alone_does_not_establish_modern_only_support(changes):
    before = cs_report()
    before["speedLayout"] = "unknown"
    with pytest.raises(ValueError):
        cs_settings_plan(changes, before)


@pytest.mark.parametrize("changes", [dict(speedMode=0, integrationTimeSeconds=1),
    dict(speedMode=2, integrationTimeSeconds=0.5), dict(syncMode=0, frequencyHz=60),
    dict(integrationTimeSeconds=0.0001), dict(frequencyHz=60.001), dict(speedMode=True),
    dict(measurementAngleDegrees=1), dict(userCalibrationChannel=11),
    dict(frequencyHz=float("nan"))])
def test_cs_complete_target_refuses_invalid_values_and_coupled_native_dependencies(changes):
    with pytest.raises(ValueError):
        cs_settings_plan(changes, cs_report())
