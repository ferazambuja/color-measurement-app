"""Pure direct-instrument settings readback and complete-target command plans.

Adapted from the current attributed MATLAB policy owners in docs/direct-serial.md.
No setting plan sends bytes. Requested changes, acknowledgements and subsequent
readback remain distinct; an unreported setting is None, never a fabricated zero.
"""

from __future__ import annotations

import re

from .decimal_numbers import decimal_integer
from .native_protocol import finite
from .serial_protocols import numbers
from .serial_transport import SerialProtocolError

MAX_SETTING_ID = 2**53 - 1  # Exact integer transport through the shared JSON/HTML client.


def _integer(value, low, high, name):
    original = value
    value = finite(value)
    if value != int(value) or value != original or not low <= value <= high:
        raise ValueError(f"{name} requires an exact integer in [{low}, {high}]")
    return int(value)


def cs_speed_values(payload, *, variation):
    """Read documented CS hardware layouts; Manual alone does not identify its generation."""
    if type(variation) is not int or variation not in (1, 2):
        raise ValueError("CS speed readback requires verified model variation 1 or 2")
    numbers(payload, cs2000=True)
    values = tuple(decimal_integer(field.strip()) for field in payload.split(","))
    mode = values[0]
    valid = len(values) <= 3 and mode in range(5)
    if valid and mode in (0, 1):
        valid = ((variation == 1 and len(values) == 1)
                 or (len(values) == 2 and values[1] in (0, 1, 2)))
    elif valid and mode in (2, 4):
        valid = (len(values) in (2, 3) and 1 <= values[1] <= 16
                 and (len(values) == 2 or values[2] in (0, 1, 2))
                 and (mode != 4 or len(values) == 3)
                 and (variation == 1 or len(values) == 3))
    elif valid:
        valid = len(values) == 3 and 5000 <= values[1] <= 120000000 and values[2] in (0, 1)
    if not valid:
        raise SerialProtocolError("SPMR returned an unsupported native speed configuration")
    interval, nd, nd_source = None, 2, "protocol-fixed-auto"
    if mode in (0, 1) and len(values) == 2:
        nd, nd_source = values[1], "reported"
    elif mode in (2, 4):
        interval = values[1]
        if len(values) == 3:
            nd, nd_source = values[2], "reported"
    elif mode == 3:
        interval, nd, nd_source = values[1] / 1e6, values[2], "reported"
    layout = ("modern" if variation == 2 or mode == 4 or (mode != 3 and nd_source == "reported")
              else "older-hardware" if mode != 3 else "unknown")
    return dict(speedMode=mode, integrationTimeSeconds=interval, internalNDMode=nd,
                internalNDModeSource=nd_source, speedLayout=layout,
                restoreCommand="SPMS," + ",".join(str(value) for value in values))


def cs_sync_values(payload):
    """SCMR supplies centihertz only for Internal sync; other modes have no frequency."""
    numbers(payload, cs2000=True)
    values = tuple(decimal_integer(field.strip()) for field in payload.split(","))
    if (values[0] not in (0, 1, 2)
            or (values[0] == 1 and (len(values) != 2 or not 2000 <= values[1] <= 20000))
            or (values[0] != 1 and len(values) != 1)):
        raise SerialProtocolError("SCMR returned an unsupported synchronization configuration")
    return dict(syncMode=values[0], frequencyHz=values[1] / 100 if values[0] == 1 else None)


def cs_settings_plan(changes, before, *, automatic=False):
    """Validate native target dependencies and ordered setters without issuing any command."""
    if not isinstance(changes, dict) or type(automatic) is not bool:
        raise ValueError("CS settings require an object and explicit automatic-policy boolean")
    ranges = dict(speedMode=(0, 4), internalNDMode=(0, 2), syncMode=(0, 2),
                  closeupLens=(0, 1), externalNDFilter=(0, 2), userCalibrationChannel=(0, 10))
    requested = (dict(speedMode=2, integrationTimeSeconds=1, internalNDMode=2, syncMode=0)
                 if automatic else {})
    requested.update(changes)
    if automatic:
        if requested["speedMode"] in (0, 1, 3) and "integrationTimeSeconds" not in changes:
            requested.pop("integrationTimeSeconds", None)
        if requested["speedMode"] == 3 and "internalNDMode" not in changes:
            requested.pop("internalNDMode", None)
    if requested.keys() - (ranges.keys() | {"integrationTimeSeconds", "frequencyHz"}):
        raise ValueError("Unknown or read-only CS setting")
    for key, value in requested.items():
        if key in ranges:
            requested[key] = _integer(value, *ranges[key], key)
        else:
            value = finite(value)
            low, high, quantum = (0.005, 120, 1000000) if key == "integrationTimeSeconds" else (
                20, 200, 100)
            if not low <= value <= high or abs(value * quantum - round(value * quantum)) > 1e-6:
                raise ValueError(f"{key} is outside its native range or resolution")
            requested[key] = value
    if not requested:
        return dict(requested={}, target=None, readback_target={}, commands=[])
    if not isinstance(before, dict) or not isinstance(before.get("values"), dict):
        raise ValueError("CS application requires current native readback")
    current = before["values"]
    needed = set(ranges) | {"integrationTimeSeconds", "frequencyHz"}
    if needed - current.keys():
        raise ValueError("CS application requires every current coupled setting")
    target = dict(current, **requested)
    if requested.keys() & {"speedMode", "integrationTimeSeconds", "internalNDMode"}:
        mode, interval, nd = (target[key] for key in (
            "speedMode", "integrationTimeSeconds", "internalNDMode"))
        if mode in (0, 1):
            if "integrationTimeSeconds" in requested:
                raise ValueError("Normal/Fast speed does not accept a fixed integration interval")
            target["integrationTimeSeconds"] = None
        else:
            if (mode != current["speedMode"] and "integrationTimeSeconds" not in requested
                    and not (mode in (2, 4) and current["speedMode"] in (2, 4))):
                raise ValueError("Changing to Manual or Multi requires an explicit interval")
            if interval is None:
                raise ValueError("The selected speed requires a known integration interval")
            if mode == 3:
                if not 0.005 <= interval <= 120 or nd not in (0, 1):
                    raise ValueError("Manual speed requires fixed ND Off/On and 0.005–120 seconds")
            elif interval != int(interval) or not 1 <= interval <= 16:
                raise ValueError("Multi speed requires 1–16 whole seconds")
        if before.get("speedLayout") != "modern" and (mode == 4 or (mode != 3 and nd != 2)):
            raise ValueError("Readback does not establish Multi-Fast or nonmanual fixed-ND support")
    if target["syncMode"] == 1:
        frequency = target["frequencyHz"]
        if frequency is None or not 20 <= frequency <= 200:
            raise ValueError("Internal synchronization requires a known 20–200 Hz frequency")
    else:
        if "frequencyHz" in requested:
            raise ValueError("A frequency override applies only to Internal synchronization")
        target["frequencyHz"] = None
    if (requested.keys() & {"closeupLens", "externalNDFilter"}
            and target["closeupLens"] == 1 and target["externalNDFilter"] != 0):
        raise ValueError("Do not combine the optional close-up lens and external ND filter")
    commands = []
    for enabled in (False, True):
        for key, prefix in (("closeupLens", "LNSS"), ("externalNDFilter", "NDFS")):
            value = target[key]
            if value != current[key] and (value != 0) == enabled:
                commands.append(dict(id=key, command=f"{prefix},{value}", value=value))
    speed = ("speedMode", "integrationTimeSeconds", "internalNDMode")
    if tuple(target[key] for key in speed) != tuple(current[key] for key in speed):
        mode = target["speedMode"]
        command = f"SPMS,{mode}"
        if mode == 3:
            command += (f",{round(target['integrationTimeSeconds'] * 1e6):09d},"
                        f"{target['internalNDMode']}")
        else:
            if mode in (2, 4):
                command += f",{int(target['integrationTimeSeconds'])}"
            if before.get("speedLayout") == "modern":
                command += f",{target['internalNDMode']}"
        commands.append(dict(id="speed", command=command,
                             value={key: target[key] for key in speed}))
    if (target["syncMode"], target["frequencyHz"]) != (current["syncMode"], current["frequencyHz"]):
        command = f"SCMS,{target['syncMode']}"
        if target["syncMode"] == 1:
            command += f",{round(target['frequencyHz'] * 100):05d}"
        commands.append(dict(id="sync", command=command,
                             value={key: target[key] for key in ("syncMode", "frequencyHz")}))
    if target["userCalibrationChannel"] != current["userCalibrationChannel"]:
        commands.append(dict(id="userCalibrationChannel",
                             command=f"UCCS,{target['userCalibrationChannel']:02d}",
                             value=target["userCalibrationChannel"]))
    preflight = []
    if target["userCalibrationChannel"] != current["userCalibrationChannel"] \
            and target["userCalibrationChannel"] != 0:
        preflight = [dict(command=f"UCPR,2,{target['userCalibrationChannel']:02d}",
                          channel=target["userCalibrationChannel"])]
    readback_target = dict(requested)
    for group in (speed, ("syncMode", "frequencyHz")):
        if requested.keys() & set(group):
            readback_target.update({key: target[key] for key in group})
    return dict(requested=requested, target=target, readback_target=readback_target,
                preflight=preflight, commands=commands)


_CR_SPECS = {
    "mode": ("Mode", "choice", (1, 16)),
    "exposureMode": ("ExposureMode", "choice", (1, 4)),
    "exposureMs": ("Exposure", "milliseconds", (1, 4)),
    "rangeMode": ("RangeMode", "choice", (1, 4)),
    "range": ("Range", "choice", (1, 4)),
    "exposureMultiplier": ("ExposureX", "positiveInteger", (1, 4)),
    "syncMode": ("SyncMode", "choice", (1, 4)),
    "syncFrequencyHz": ("SyncFreq", "hertz", (1, 4)),
    "speed": ("Speed", "choice", (1, 17)),
    "accessory": ("Accessory", "choice", (1, 4)),
    "filter1": ("Filter1", "filter", (1, 4)),
    "filter2": ("Filter2", "filter", (1, 4)),
    "filter3": ("Filter3", "filter", (1, 4)),
    "userCalibrationMode": ("UserCalibMode", "choice", (1, 16)),
    "matrixId": ("Matrix", "index", (1, 4)),
    "matchId": ("Match", "index", (1, 16)),
    "maxAutoExposureMs": ("MaxAutoExposure", "milliseconds", (1, 26)),
}


def cr_settings_specs(model):
    """Remote 1.36 command ownership and model/firmware applicability."""
    if not isinstance(model, str) or model not in {"CR-100", "CR-250", "CR-300"}:
        raise ValueError("Choose CR-100, CR-250 or CR-300")
    excluded = {"speed"} if model == "CR-100" else {"mode", "rangeMode", "range", "matrixId"}
    return {key: dict(token=token, kind=kind, since=since)
            for key, (token, kind, since) in _CR_SPECS.items() if key not in excluded}


def cr_setting_list(payload, lines, *, columns):
    """Validate counted RC rows; retain reported IDs, labels and quantity-type text."""
    if type(columns) is not int or columns not in (2, 3) or not isinstance(lines, (list, tuple)):
        raise ValueError("CR lists require complete rows and two or three columns")
    try:
        if not isinstance(payload, str):
            raise ValueError("CR configuration count requires native text")
        count = (0 if payload == "None" else
                 _integer(decimal_integer(payload.strip()), 0, 256, "row count"))
        if len(lines) != count:
            raise ValueError("CR configuration list differs from its declared row count")
        choices, seen = [], set()
        for line in lines:
            if not isinstance(line, str) or any(not 32 <= ord(char) <= 126 for char in line):
                raise ValueError("CR configuration rows require printable ASCII")
            fields = line.split(",")
            if len(fields) != columns or not fields[1]:
                raise ValueError("CR configuration row has malformed fields")
            value = _integer(decimal_integer(fields[0].strip()), 0, MAX_SETTING_ID, "setting ID")
            if value in seen:
                raise ValueError("CR configuration list has duplicate IDs")
            seen.add(value)
            choice = dict(value=value, label=fields[1])
            if columns == 3:
                choice["quantity_type"] = fields[2]
            choices.append(choice)
        return choices
    except ValueError as error:
        raise SerialProtocolError(str(error)) from error


def cr_setting_value(payload, key, control):
    """Interpret RS; NA stays unavailable and maximum Auto exposure has no RS readback."""
    if key not in _CR_SPECS and key != "aperture":
        raise ValueError("Unknown CR setting readback")
    if key == "maxAutoExposureMs":
        raise ValueError("CR maximum Auto exposure has no documented current readback")
    if not isinstance(payload, str):
        raise SerialProtocolError("CR readback requires text")
    payload = payload.strip()
    if payload == "NA":
        return None
    if not payload or any(not 32 <= ord(char) <= 126 for char in payload):
        raise SerialProtocolError("CR readback requires nonempty printable ASCII")
    if key == "aperture":
        return payload
    kind = _CR_SPECS[key][1]
    try:
        if kind == "filter":
            fields = payload.split(",")
            if len(fields) != 3:
                raise ValueError("RS Filter requires three slots")
            payload = fields[int(key[-1]) - 1]
        if kind in {"choice", "filter"}:
            if key in {"exposureMode", "rangeMode"} and payload == "Manual":
                payload = "Fixed"
            matches = [choice["value"] for choice in control["choices"]
                       if choice["label"] == payload]
            if len(matches) != 1:
                raise ValueError("CR readback name has no unique reported ID")
            return matches[0]
        if kind == "index":
            value = _integer(decimal_integer(payload), 0, MAX_SETTING_ID, key)
            if value not in [choice["value"] for choice in control["choices"]]:
                raise ValueError("CR readback profile ID is not configured")
            return value
        if kind in {"milliseconds", "hertz"}:
            suffix = "msec" if kind == "milliseconds" else "Hz"
            match = re.fullmatch(r"(.+)\s+" + suffix, payload)
            if match is None:
                raise ValueError("CR readback lacks its documented numeric unit suffix")
            value = numbers(match[1].strip(), 1)[0]
        else:
            value = _integer(decimal_integer(payload), 1, MAX_SETTING_ID, key)
        if value < 0:
            raise ValueError("CR setting readback requires a nonnegative number")
        return value
    except ValueError as error:
        raise SerialProtocolError(str(error)) from error


def cr_setting_limits(key, minimum, maximum):
    """Usable RC numeric bounds after their full frames have been authenticated.

    Invalid or unreported capabilities disable controls; original raw responses
    remain at the transaction owner. This does not soften current RS parsing.
    """
    if key not in _CR_SPECS or _CR_SPECS[key][1] not in {
        "milliseconds", "hertz", "positiveInteger",
    }:
        raise ValueError("Choose a numeric CR setting for native limits")
    unavailable = dict(available=False, minimum=None, maximum=None)
    if minimum is None or maximum is None:
        return dict(unavailable, unavailable_reason=f"CR {key} numeric bounds are unsupported")
    read_key = "exposureMs" if key == "maxAutoExposureMs" else key
    try:
        low = cr_setting_value(minimum, read_key, {})
        high = cr_setting_value(maximum, read_key, {})
    except SerialProtocolError:
        return dict(
            unavailable, unavailable_reason=f"CR {key} numeric bounds could not be interpreted",
        )
    if low is None or high is None:
        return dict(unavailable, unavailable_reason=f"CR {key} numeric bounds were not reported")
    if low <= 0 or low > high:
        return dict(
            unavailable, unavailable_reason=f"CR {key} native limits must be positive and ordered",
        )
    return dict(available=True, minimum=low, maximum=high)


def cr_settings_plan(model, changes, before, *, automatic=False):
    """Validate the complete target and source-backed setter order without serial I/O."""
    specs = cr_settings_specs(model)
    if (not isinstance(changes, dict) or type(automatic) is not bool
            or changes.keys() - (specs.keys() - {"mode"})):
        raise ValueError("Unknown, read-only or model-inapplicable CR setting")
    if (not isinstance(before, dict) or not isinstance(before.get("values"), dict)
            or not isinstance(before.get("controls"), dict)):
        raise ValueError("CR application requires current settings and reported capabilities")
    controls, current = before["controls"], before["values"]

    def option(key, name, *, read_only=False):
        control = controls.get(key, {})
        matches = [item["value"] for item in control.get("choices", []) if item["label"] == name]
        if (not read_only and control.get("available") is not True) or len(matches) != 1:
            raise ValueError(f"CR {key} requires one supported reported {name} option")
        return matches[0]

    requested = {}
    if automatic:
        requested["exposureMode"] = option("exposureMode", "Auto")
        if model == "CR-100":
            requested.update(mode=option("mode", "Colorimeter", read_only=True),
                             rangeMode=option("rangeMode", "Auto"))
    requested.update(changes)
    for key, value in requested.items():
        control = controls.get(key, {})
        if key != "mode" and control.get("available") is not True:
            raise ValueError(f"CR {key} is unavailable on this instrument")
        kind = specs[key]["kind"]
        if kind in {"choice", "filter", "index", "positiveInteger"}:
            value = _integer(value, 1 if kind == "positiveInteger" else 0, MAX_SETTING_ID, key)
        else:
            value = finite(value)
        if kind in {"choice", "filter", "index"}:
            valid = value in [item["value"] for item in control.get("choices", [])]
        else:
            low, high = finite(control.get("minimum")), finite(control.get("maximum"))
            valid = 0 < low <= value <= high
        if not valid:
            raise ValueError(f"CR {key} is outside reported choices or bounds")
        requested[key] = value
    target = dict(current, **requested)

    def label(key):
        matches = [item["label"] for item in controls.get(key, {}).get("choices", [])
                   if item["value"] == target.get(key)]
        return matches[0] if len(matches) == 1 else None

    exposure, correction = label("exposureMode"), label("userCalibrationMode")
    fixed = {"Fixed", "Manual"}

    def require_bound(key):
        control = controls.get(key, {})
        if control.get("available") is not True:
            raise ValueError(f"CR manual mode requires available {key} bounds")
        if target.get(key) is None:
            raise ValueError(f"CR manual mode requires a known {key} within reported bounds")
        low, high, value = (finite(control.get("minimum")), finite(control.get("maximum")),
                            finite(target.get(key)))
        if not 0 < low <= value <= high:
            raise ValueError(f"CR manual mode requires {key} within reported bounds")

    if (exposure in fixed
            and requested.keys() & {"exposureMs", "exposureMode", "rangeMode", "range"}):
        require_bound("exposureMs")
    if label("syncMode") == "Manual" and requested.keys() & {"syncMode", "syncFrequencyHz"}:
        require_bound("syncFrequencyHz")
    if "exposureMs" in requested and exposure not in fixed:
        raise ValueError("CR exposureMs requires known Fixed exposure")
    if (model == "CR-100" and exposure in fixed
            and requested.keys() & {"exposureMs", "exposureMode", "rangeMode", "range"}
            and (label("rangeMode") not in fixed or label("range") != "D")):
        raise ValueError("CR-100 Fixed exposure requires Fixed range D")
    if "range" in requested and label("rangeMode") not in fixed:
        raise ValueError("CR range requires Fixed range mode")
    if "syncFrequencyHz" in requested and label("syncMode") != "Manual":
        raise ValueError("CR syncFrequencyHz requires Manual synchronization")
    if "maxAutoExposureMs" in requested and exposure != "Auto":
        raise ValueError("CR maximum Auto exposure requires Auto exposure")
    if model != "CR-100" and requested.get("userCalibrationMode") is not None \
            and correction == "Matrix":
        raise ValueError("CR Matrix correction is a colorimeter-only setting")
    for key, mode in (("matrixId", "Matrix"), ("matchId", "Match")):
        if key in requested and correction != mode:
            raise ValueError(f"CR {key} requires {mode} correction mode")
        if "userCalibrationMode" in requested and correction == mode and target.get(key) is None:
            raise ValueError(f"CR {mode} correction requires a reported {key}")
    changed_accessory = ("accessory" in requested
                         and requested["accessory"] != current.get("accessory"))
    if changed_accessory and (correction == "Matrix" or "matrixId" in requested):
        raise ValueError("CR accessory-specific matrices require a separate accessory change")
    order = ["accessory", "filter1", "filter2", "filter3", "rangeMode", "range", "exposureMs",
             "exposureMode", "exposureMultiplier", "syncFrequencyHz", "syncMode", "speed",
             "matrixId", "matchId", "userCalibrationMode", "maxAutoExposureMs"]
    if "exposureMode" in requested and exposure == "Auto":
        order.remove("exposureMode")
        order.insert(0, "exposureMode")
    if "userCalibrationMode" in requested and correction == "None":
        order.remove("userCalibrationMode")
        order.insert(0, "userCalibrationMode")
    if "mode" in requested:
        order.insert(0, "mode")
    commands = [dict(id=key, command=f"SM {specs[key]['token']} {requested[key]:.17g}",
                     value=requested[key]) for key in order
                if key in requested and (key == "maxAutoExposureMs"
                                         or requested[key] != current.get(key))]
    return dict(requested=requested, target=target, commands=commands)


def direct_settings_options(model, report):
    """Frontend controls from these same native policies; null means no explicit override."""
    if not isinstance(report, dict) or not isinstance(report.get("values"), dict):
        raise ValueError("Direct controls require current native settings")
    rows = [dict(id="automatic", label="Use normal acquisition recipe", type="boolean",
                 default=True, current=None, available=True)]
    current = report["values"]
    if model in {"CR-100", "CR-250", "CR-300"}:
        for key, spec in cr_settings_specs(model).items():
            if key == "mode":
                continue
            control = report.get("controls", {}).get(key, {})
            row = dict(id=key, label=key, default=None, current=current.get(key),
                       type="choice" if spec["kind"] in {"choice", "index", "filter"} else
                       "integer" if spec["kind"] == "positiveInteger" else "number",
                       available=control.get("available") is True,
                       unavailable_reason=control.get("unavailable_reason"))
            if row["type"] == "choice":
                row.update(choices=[item["value"] for item in control.get("choices", [])],
                           choice_details=control.get("choices", []))
            else:
                row.update(minimum=control.get("minimum"), maximum=control.get("maximum"))
            rows.append(row)
    elif model in {"CS-2000", "CS-2000A"}:
        choices = dict(speedMode=[(0, "Normal"), (1, "Fast"), (2, "Multi-Normal"), (3, "Manual")],
                       syncMode=[(0, "No Sync"), (1, "Internal"), (2, "External")],
                       closeupLens=[(0, "Off"), (1, "On")],
                       externalNDFilter=[(0, "Off"), (1, "1/10"), (2, "1/100")],
                       internalNDMode=[(0, "Off"), (1, "On")],
                       userCalibrationChannel=[(value, str(value)) for value in range(11)])
        if report.get("speedLayout") == "modern":
            choices["speedMode"].append((4, "Multi-Fast"))
            choices["internalNDMode"].append((2, "Auto"))
        for key, values in choices.items():
            rows.append(dict(id=key, label=key, type="choice", default=None,
                             current=current.get(key), available=True,
                             choices=[value for value, _ in values],
                             choice_details=[dict(value=value, label=label)
                                             for value, label in values]))
        rows.extend([
            dict(id="integrationTimeSeconds", label="Integration or averaging interval (s)",
                 type="number", default=None, current=current.get("integrationTimeSeconds"),
                 available=True, minimum=0.005, maximum=120),
            dict(id="frequencyHz", label="Internal synchronization frequency (Hz)", type="number",
                 default=None, current=current.get("frequencyHz"), available=True,
                 minimum=20, maximum=200),
        ])
    else:
        raise ValueError("Unknown direct model")
    for row in rows:
        reason = (None if row["available"] else row.get("unavailable_reason")
                  or "No current supported native choices or bounds were reported")
        row.update(unavailable_reason=reason,
                   by_mode=dict(emissive_spot=dict(
                       available=row["available"], unavailable_reason=reason,
                       **{key: row[key] for key in (
                           "choices", "choice_details", "minimum", "maximum")
                          if key in row},
                   )))
    return rows
