"""Source-bound chart context from generic descriptors and original terminal records.

Requested configuration, driver acknowledgement and actual readback remain
different facts. No instrument query, physical-equivalence inference or science
calculation occurs. The application authenticates the original source records.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from .argyll_capability import MODE_FLAGS, parse_mode_name, route_illumination
from .argyll_description import validate_native_identity
from .chart_state import validate_context
from .instrument_frontend import capability_identity
from .json_values import unique_object
from .models import DeviceIdentity, Observation, identity_agrees
from .operation_records import OperationResult, result_from_json

_IDENTITY = {
    "route",
    "origin",
    "device_id",
    "model",
    "serial",
    "serial_encoding",
    "backend",
    "backend_version",
    "driver",
}
_NATIVE5 = {"xyz_unit", "xyz_scale", "observer", "illuminant", "actual_mode"}
_DECLARABLE = _NATIVE5 | {
    "mode",
    "workflow",
    "optical_mode",
    "native_mtype",
    "native_mcond",
    "calibration",
    "correction_applied",
    "factory_calibration",
    "native_display_type",
    "spectral_quantity",
    "spectral_unit",
    "spectral_grid_nm",
    "spectrum_norm",
    "geometry",
    "backing",
    "lighting",
    "specimen_id",
    "edition",
}
_UNKNOWN = {"unknown", "unspecified", "not reported", ""}


def _json(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    )


def _digest(value):
    return hashlib.sha256(_json(value).encode("ascii")).hexdigest()


def _object(value):
    if isinstance(value, str):
        value = json.loads(value, object_pairs_hook=unique_object)
    return json.loads(_json(value), object_pairs_hook=unique_object)


def _established(value):
    return value is not None and not (isinstance(value, str) and value.strip().lower() in _UNKNOWN)


def _fact(value=None, certainty="unknown", source="Not reported by the retained source"):
    return dict(
        value=value, certainty=certainty if _established(value) else "unknown", source=source
    )


def _inputs(descriptor, request):
    descriptor, request = _object(descriptor), _object(request)
    if not isinstance(descriptor, dict) or not isinstance(request, dict):
        raise ValueError("Chart context requires a descriptor and original request objects")
    if (
        descriptor.get("contract")
        not in {"argyll_generic_instrument_v1", "direct_serial_instrument_v1"}
        or not isinstance(descriptor.get("device"), dict)
        or request.get("device_id") != descriptor.get("device_id")
        or request.get("capability_id") != descriptor.get("capability_id")
    ):
        raise ValueError("Chart request does not bind this generic descriptor")
    body = {key: value for key, value in descriptor.items() if key != "capability_id"}
    expected = capability_identity(body)
    if expected != descriptor["capability_id"]:
        raise ValueError("Chart descriptor content differs from its capability identity")
    if any(
        not isinstance(request.get(key), str) or not request[key].strip()
        for key in ("mode", "workflow")
    ) or not isinstance(request.get("options"), dict):
        raise ValueError("Chart context needs mode, workflow and explicit request options")
    offered = [row for row in descriptor.get("modes", []) if row.get("mode") == request["mode"]]
    if len(offered) != 1 or request["workflow"] not in {
        row["workflow"] for row in offered[0].get("workflows", [])
    }:
        raise ValueError("Chart request mode/workflow is outside its retained descriptor")
    return descriptor, request


def _optical_mode(mode):
    try:
        return route_illumination(parse_mode_name(mode)[0])
    except ValueError:
        return None


def _current_identity(descriptor):
    """Consume caller-authenticated current binding metadata without querying a device."""
    device = descriptor["device"]
    if device.get("device_id") != descriptor["device_id"]:
        raise ValueError("Chart current binding device identity differs from its descriptor")
    if descriptor["contract"] == "direct_serial_instrument_v1":
        details = descriptor.get("identity_details")
        if details is None:
            return {}, {}
        if (not isinstance(details, dict) or device.get("backend") != "direct_serial"
                or details.get("model") != device.get("model")
                or _json(details.get("serial")) != _json(device.get("serial"))):
            raise ValueError("Chart current binding direct identity differs from its descriptor")
        material = dict(driver=_fact("direct_serial", "acknowledged",
                                    "Current direct software backend identity"))
        for key in {"model", "serial", "firmware", "variation"} & details.keys():
            material["native_identity:" + key] = _fact(
                details[key], "readback", "Current bound direct identification replies",
            )
        return material, dict(current_direct_identity=details)
    packet = descriptor.get("current_binding_identity")
    if packet is None:
        return {}, {}
    if (not isinstance(packet, dict)
            or set(packet) != {"device_id", "initialized_type", "native_identity", "provenance"}
            or packet["device_id"] != device["device_id"]
            or type(packet["initialized_type"]) is not int
            or not 0 < packet["initialized_type"] <= 0x7fffffff):
        raise ValueError("Malformed chart current binding identity")
    provenance = packet["provenance"]
    receipts = {"description_helper_sha256", "session_helper_sha256", "build_report_sha256"}
    if (not isinstance(provenance, dict) or set(provenance) != receipts | {"backend_version"}
            or provenance["backend_version"] != device.get("backend_version")
            or any(not isinstance(provenance[key], str) or len(provenance[key]) != 64
                   or any(character not in "0123456789abcdef" for character in provenance[key])
                   for key in receipts
                   if key != "build_report_sha256" or provenance[key] is not None)):
        raise ValueError("Malformed chart current binding provenance")
    native = validate_native_identity(packet["native_identity"], serial=device.get("serial"))
    if native is None:
        return {}, dict(current_binding_identity=packet)
    from .instrument import _bound_native_family

    _bound_native_family(native, DeviceIdentity(**device))
    material = dict(driver=_fact(native["driver"], "readback",
                                "Current verified initialized instance driver"))
    for key, value in native["identity"].items():
        material["native_identity:" + key] = _fact(
            value, "readback", "Current initialized instance identity adapter",
        )
    return material, dict(current_binding_identity=packet)


def _pr_setup_material(setup, phase, source):
    """Project reported setup without decoding Gain or inventing unknown flags."""
    material = {}
    for name, value in setup.items():
        if name == "descriptions":
            # Opaque descriptions remain in the original retained report.
            continue
        if (name == "adaptive" and setup.get("adaptive_known") is not True
                or name == "extended" and setup.get("sensitivity_known") is not True):
            value = None
        material[f"native_setting:{phase}:{name}"] = _fact(
            dict(value=value, presence="present") if value is not None else None,
            "readback", source,
        )
    return material


def _current_pr_settings(descriptor):
    """Fence continuation against the bound frontend's last validated settings read.

    This cache is current intent evidence, not a future capture or an accepted
    reading. Accepted context always replaces it from original capture readbacks.
    A failed/unknown report establishes no current settings.
    """
    profile = descriptor.get("native_controls")
    if not isinstance(profile, dict) or profile.get("driver") != "pr655":
        return {}
    native = (descriptor.get("current_binding_identity") or {}).get("native_identity")
    if not isinstance(native, dict) or native.get("driver") != "pr655":
        return {}
    report = profile.get("current")
    if (not isinstance(report, dict) or report.get("driver") != "pr655"
            or report.get("failed") is not False or type(report.get("status_code")) is not int
            or report["status_code"] != 0 or report.get("setup_valid") is not True):
        return {}
    from .instrument import _pr_control_setup

    setup = _pr_control_setup(report.get("setup"), True)
    return {key: value for phase in ("setup_before", "setup_after")
            for key, value in _pr_setup_material(
                setup, phase, "Last bound native settings readback; future capture is independent",
            ).items()}


def intended(descriptor, request):
    """Describe selected identity and requested intent without predicting native data.

    Descriptor identity is acknowledged bound metadata; a missing serial remains
    unknown. Mode, optical route and every explicit option remain requested.
    Driver/native identity comes only from current initialized-instance metadata
    or current direct identification tied to this DeviceIdentity. Receipts and
    observed initialized type remain provenance, not scientific equivalence.
    Native XYZ/spectrum/calibration remain unknown before acquisition. A successful
    bound settings report can supply separate last-observed readback fences; it
    does not predict the future capture or become an accepted reading.
    Required intent fields contain current identity and mode/workflow only; later
    native enrichment does not automatically split a pass. Source continuity
    independently rechecks each original selected reading's required facts.
    """
    descriptor, request = _inputs(descriptor, request)
    device = descriptor["device"]
    material = {
        key: _fact(device.get(key), "acknowledged", "Bound descriptor.device")
        for key in ("device_id", "model", "serial", "serial_encoding", "backend", "backend_version")
    }
    material.update(
        route=_fact(
            "DIRECT_SERIAL" if descriptor["contract"] == "direct_serial_instrument_v1"
            else "ARGYLL", "derived", "Retained descriptor contract",
        ),
        origin=_fact(descriptor.get("provenance"), "acknowledged", "Descriptor provenance"),
        mode=_fact(request["mode"], "requested", "Original request.mode"),
        workflow=_fact(request["workflow"], "requested", "Original request.workflow"),
        optical_mode=_fact(_optical_mode(request["mode"]), "requested", "Requested mode grammar"),
    )
    for key in _NATIVE5 | {
        "native_mtype",
        "native_mcond",
        "calibration",
        "correction_applied",
        "factory_calibration",
        "driver",
        "spectral_quantity",
        "spectral_unit",
        "spectral_grid_nm",
        "spectrum_norm",
        "geometry",
        "backing",
        "lighting",
    }:
        material[key] = _fact()
    for key, value in request["options"].items():
        material[f"requested_option:{key}"] = _fact(value, "requested", "Original request.options")
        material[f"effective_option:{key}"] = _fact()
    identity, binding_provenance = _current_identity(descriptor)
    material.update(identity)
    material.update(_current_pr_settings(descriptor))
    return validate_context(
        dict(
            phase="intended",
            material=material,
            required_material=sorted(
                ((set(material) & _IDENTITY) - {"driver"}) | set(identity) | {"mode", "workflow"}
            ),
            provenance=dict(
                descriptor_sha256=_digest(descriptor),
                request_sha256=_digest(request),
                observation_provenance=descriptor.get("provenance"),
                **binding_provenance,
            ),
        )
    )


def _retained_json(configuration, key, issues):
    if key not in configuration:
        return None
    try:
        return _object(configuration[key])
    except (ValueError, TypeError, RecursionError) as error:
        issues.append(f"Retained {key} cannot be interpreted: {error}")
        return None


def source_identity_keys(observation, accepted_context):
    """Name identity facts whose values match their original retained source.

    Accept an original typed color observation or its caller-validated canonical
    snapshot. Device binding comes from device_id; driver/native_identity fields
    come from native_facts_json for Argyll, or direct_identity_json and the
    recorded software backend for direct serial. Missing driver metadata can corroborate an
    unknown driver fact. A mere identity-shaped field name is insufficient.
    This is attribution for color comparison, not physical device equivalence or
    permission to relax the state owner's stricter continuation identity checks.
    """
    record = asdict(observation) if isinstance(observation, Observation) else observation
    if not isinstance(record, dict) or not isinstance(record.get("device"), dict):
        raise ValueError("Identity attribution needs the original canonical color observation")
    context = validate_context(accepted_context)
    facts = _retained_json(dict(record["configuration"]), "native_facts_json", [])
    identities = dict(
        device_id=record["device"]["device_id"],
        driver=facts.get("driver") if isinstance(facts, dict) else None,
    )
    if isinstance(facts, dict) and isinstance(facts.get("identity"), dict):
        identities.update(
            {f"native_identity:{key}": value for key, value in facts["identity"].items()}
        )
    direct = _retained_json(dict(record["configuration"]), "direct_identity_json", [])
    if record["device"].get("backend") == "direct_serial" and isinstance(direct, dict):
        identities["driver"] = record["device"]["backend"]
        identities.update({
            f"native_identity:{key}": value for key, value in direct.items()
            if key in {"model", "serial", "firmware", "variation"}
        })
    return {
        key
        for key, value in identities.items()
        if key in context["material"] and _json(value) == _json(context["material"][key]["value"])
    }


def accepted(descriptor, request, result, observation_id=None):
    """Build acquired context from one exact retained observation or no-reading outcome.

    Color, scalar, timing and failed/no-reading outcomes remain distinct. Native5
    comes only from original XYZ and actual_mode; no mode-derived XYZ is invented.
    Exact native spectrum grid and configuration norm are preserved separately.
    Native settings retain per-key presence/value and readback certainty, with
    driver_set kept separately as provenance rather than a material difference;
    neither a successful setter nor a valid calibration label proves optical or
    factory equivalence. Original records remain owned by the caller.
    """
    descriptor, request = _inputs(descriptor, request)
    if isinstance(result, (str, dict)):
        result = result_from_json(result if isinstance(result, str) else _json(result))
    if not isinstance(result, OperationResult):
        raise ValueError("Accepted context requires the original typed terminal result")
    if _json(_object(result.request_json)) != _json(request):
        raise ValueError("Terminal result does not bind the exact chart request")
    if not identity_agrees(result.device, DeviceIdentity(**descriptor["device"])):
        raise ValueError("Terminal identity differs from the bound chart descriptor")
    matches = [
        patch.observation
        for patch in result.patches
        if patch.observation.observation_id == observation_id
    ]
    if observation_id is None and result.patches:
        if len(result.patches) != 1:
            raise ValueError("Choose the exact retained observation for acquired context")
        matches = [result.patches[0].observation]
    if observation_id is not None and len(matches) != 1:
        raise ValueError("Chart observation is absent from the original terminal result")
    observation = matches[0] if matches else None
    context = intended(descriptor, request)
    context["phase"] = "accepted"
    material, issues = context["material"], []
    # A descriptor's last settings query is intent evidence, never acquired
    # context. Missing/partial original phases must not inherit cached readback.
    for key in tuple(material):
        if key.startswith(("native_setting:setup_before:", "native_setting:setup_after:")):
            del material[key]
    setting_writes = {}
    device = result.device if observation is None else observation.device
    for key in ("device_id", "model", "serial", "serial_encoding", "backend", "backend_version"):
        material[key] = _fact(
            getattr(device, key),
            "acknowledged" if key != "serial" and key != "serial_encoding" else "readback",
            "Original terminal/observation device identity",
        )
    material["origin"] = _fact(result.provenance, "acknowledged", "Original terminal provenance")
    material["workflow"] = _fact(result.workflow, "acknowledged", "Original terminal workflow")
    if observation is not None:
        for key in ("mode", "actual_mode"):
            material[key] = _fact(
                observation.actual_mode, "readback", "Original observation.actual_mode"
            )
        material["optical_mode"] = _fact(
            _optical_mode(observation.actual_mode), "derived", "Actual mode grammar"
        )
    configuration = dict(result.configuration)
    if observation is not None:
        # Per-reading native facts outrank operation context, never a later pass.
        configuration.update(dict(observation.configuration))
    calibration = result.calibration if observation is None else observation.calibration
    material["calibration"] = _fact(
        calibration.status, "readback", "Original calibration status; driver assessment only"
    )
    for key in ("native_mtype", "native_mcond"):
        value = configuration.get(key)
        try:
            value = int(value) if isinstance(value, str) and str(int(value)) == value else None
        except ValueError:
            value = None
        material[key] = _fact(value, "readback", f"Original observation.configuration.{key}")
    material["correction_applied"] = _fact(
        configuration.get("correction_applied"),
        "acknowledged",
        "Original observation driver-applied correction",
    )
    if isinstance(observation, Observation):
        native = observation.backend_reported
        if native.xyz is not None:
            for key, attribute in (
                ("xyz_unit", "unit"),
                ("xyz_scale", "scale"),
                ("observer", "observer"),
                ("illuminant", "illuminant"),
            ):
                material[key] = _fact(
                    getattr(native.xyz, attribute), "readback", "Original native XYZ context"
                )
        if native.spectrum is not None:
            for key, value in (
                ("spectral_quantity", native.spectrum.quantity),
                ("spectral_unit", native.spectrum.unit),
                ("spectral_grid_nm", list(native.spectrum.wavelengths_nm)),
            ):
                material[key] = _fact(value, "readback", "Original native spectrum context")
            raw_norm = configuration.get("spectrum_norm")
            try:
                norm = float(raw_norm) if raw_norm is not None else None
                if norm is not None and (not 0 < norm < float("inf")):
                    norm = None
            except (ValueError, TypeError, OverflowError):
                norm = None
            material["spectrum_norm"] = _fact(
                norm, "readback", "Original configuration.spectrum_norm"
            )
    facts = _retained_json(configuration, "native_facts_json", issues)
    if isinstance(facts, dict):
        material["driver"] = _fact(facts.get("driver"), "readback", "Original native_facts driver")
        material["factory_calibration"] = _fact(
            facts.get("factory_calibration"),
            "readback",
            "Original native_facts factory calibration disclosure",
        )
        identity = facts.get("identity")
        identity = identity if isinstance(identity, dict) else {}
        for key, value in identity.items():
            material[f"native_identity:{key}"] = _fact(
                value, "readback", "Original native_facts identity"
            )
        settings = facts.get("settings")
        settings = settings if isinstance(settings, list) else []
        for setting in settings:
            if not isinstance(setting, dict) or not isinstance(setting.get("key"), str):
                issues.append("Malformed retained native setting")
                continue
            known = (
                setting.get("presence") == "not_applicable"
                or setting.get("presence") == "present"
                and _established(setting.get("value"))
            )
            value = {key: setting.get(key) for key in ("value", "presence")} if known else None
            key = f"native_setting:{setting['key']}"
            if key in material:
                raise ValueError("Retained native settings repeat an effective key")
            material[key] = _fact(value, "readback", "Original native_facts setting readback")
            setting_writes[setting["key"]] = setting.get("driver_set")
        if facts.get("driver") == "pr655":
            capture = facts.get("capture")
            capture = capture if isinstance(capture, dict) else {}
            for phase in ("setup_before", "setup_after"):
                setup = capture.get(phase)
                if not isinstance(setup, dict):
                    material[f"native_setting:{phase}:unavailable"] = _fact(
                        source=f"Original PR same-capture {phase} is unavailable",
                    )
                    continue
                material.update(_pr_setup_material(
                    setup, phase, f"Original PR same-capture {phase} readback",
                ))
        controls = _retained_json(configuration, "native_control_json", issues)
        changes = request["options"].get("native_controls")
        if isinstance(changes, dict) and isinstance(controls, dict):
            from .instrument import _control_values
            from .native_controls import NativeControls

            try:
                asked = NativeControls.from_changes(facts["driver"], changes)
                current = _control_values(controls.get("values"), driver=asked.driver)
                confirmed = controls.get("confirmed")
                if (controls.get("driver") == asked.driver and controls.get("failed") is False
                        and type(confirmed) is int and confirmed & asked.present == asked.present
                        and current["present"] & asked.present == asked.present):
                    material["effective_option:native_controls"] = _fact(
                        {key: current[key] for key, _ in asked.fields}, "readback",
                        "Original same-operation independently confirmed native settings",
                    )
            except (ValueError, KeyError, TypeError) as error:
                issues.append(f"Native settings readback is unavailable: {error}")
    if descriptor["contract"] == "direct_serial_instrument_v1":
        material["driver"] = _fact(
            result.device.backend, "acknowledged",
            "Original direct observation software backend identity",
        )
        if observation is not None:
            for key in ("mode", "actual_mode"):
                material[key] = _fact(
                    observation.actual_mode, "acknowledged", "Original direct acquisition route",
                )
        identity = _retained_json(configuration, "direct_identity_json", issues)
        if isinstance(identity, dict):
            for key in {"model", "serial", "firmware", "variation"} & identity.keys():
                material[f"native_identity:{key}"] = _fact(
                    identity[key], "readback", "Original direct identification replies",
                )
        receipt = _retained_json(configuration, "direct_settings_json", issues)
        after = receipt.get("after") if isinstance(receipt, dict) else None
        values = after.get("values") if isinstance(after, dict) else None
        if isinstance(values, dict):
            for key, value in values.items():
                material[f"native_setting:{key}"] = _fact(
                    dict(value=value, presence="present") if value is not None else None,
                    "readback", "Original direct independent current-setting reply",
                )
            for key in request["options"]:
                if key == "automatic":
                    material[f"effective_option:{key}"] = _fact(
                        receipt.get("automatic"), "acknowledged",
                        "Original software recipe receipt",
                    )
                else:
                    material[f"effective_option:{key}"] = _fact(
                        values.get(key), "readback",
                        "Original direct independent current-setting reply",
                    )
        capture = _retained_json(configuration, "direct_capture_json", issues)
        if isinstance(capture, dict):
            for key in ("speed_mode", "sync_mode", "integration_time_microseconds",
                        "internal_nd_filter", "closeup_lens", "external_nd_filter",
                        "measurement_angle", "user_calibration_channel"):
                if key in capture:
                    material[f"native_capture_setting:{key}"] = _fact(
                        capture[key], "readback", "Original CS MEDR capture conditions",
                    )
        if isinstance(observation, Observation):
            native = observation.backend_reported
            if native.xyz is not None and native.xyz.scale == "unverified":
                for key in ("xyz_unit", "xyz_scale"):
                    material[key] = _fact(source="Direct native XYZ has unverified units/scale")
            if native.spectrum is not None and native.spectrum.unit.startswith("unverified"):
                material["spectral_unit"] = _fact(
                    source="Direct spectral density units are unverified",
                )
                if native.spectrum.quantity.startswith("unverified"):
                    material["spectral_quantity"] = _fact(
                        source="Direct native spectral quantity is unverified",
                    )
    raw = (
        None
        if observation is None
        else _retained_json(
            {"raw_backend_output": observation.raw_backend_output}, "raw_backend_output", issues
        )
    )
    connected = (
        next(
            (
                event
                for event in reversed(raw.get("events", []))
                if isinstance(event, dict) and event.get("event") == "connected"
            ),
            None,
        )
        if isinstance(raw, dict)
        else None
    )
    if connected is not None:
        for key in ("mode", "spectral", "trigger", "refresh_rate_override_hz"):
            material[f"native_connected:{key}"] = _fact(
                connected.get(key), "readback", "Original connected event"
            )
        if type(connected.get("mode")) is int and "highres" in request["options"]:
            material["effective_option:highres"] = _fact(
                bool(connected["mode"] & MODE_FLAGS["highres"]),
                "readback",
                "Original connected native mode bit",
            )
        display = connected.get("display_types")
        if isinstance(display, dict):
            material["native_display_type"] = _fact(
                display.get("readback"),
                "readback",
                "Original connected display calibration readback",
            )
            if "display_type" in request["options"]:
                material["effective_option:display_type"] = _fact(
                    display.get("applied_choice_id"),
                    "acknowledged",
                    "Original connected applied display choice",
                )
    required = set(context["required_material"]) | {"actual_mode", "optical_mode", "calibration"}
    if isinstance(observation, Observation) and observation.backend_reported.xyz is not None:
        required |= _NATIVE5
    if isinstance(observation, Observation) and observation.backend_reported.spectrum is not None:
        required |= {"spectral_quantity", "spectral_unit", "spectral_grid_nm", "spectrum_norm"}
    required |= {key for key, fact in material.items() if key.startswith("native_setting:")}
    required |= {key for key in material if key.startswith("native_capture_setting:")}
    required |= {
        key
        for key in ("native_mtype", "native_mcond", "correction_applied", "driver")
        if material[key]["certainty"] != "unknown"
    }
    required |= {key for key in material if key.startswith("effective_option:")}
    if (
        "native_display_type" in material
        and material["native_display_type"]["certainty"] != "unknown"
    ):
        required.add("native_display_type")
    context["required_material"] = sorted(required)
    context["provenance"].update(
        result_sha256=_digest(asdict(result)),
        operation_id=result.operation_id,
        observation_id=None if observation is None else observation.observation_id,
        observation_sha256=None if observation is None else _digest(asdict(observation)),
        observation_provenance=result.provenance if observation is None else observation.provenance,
        calibration_detail=calibration.detail,
        native_setting_driver_set=setting_writes,
        interpretation_issues=issues,
    )
    for key in (
        "calibration_events_json", "calibration_outcome_json", "native_facts_json",
        "native_control_json",
        "direct_identity_json", "direct_settings_json", "direct_capture_json",
    ):
        if key in configuration:
            context["provenance"][f"{key}_sha256"] = hashlib.sha256(
                configuration[key].encode("utf-8")
            ).hexdigest()
    return validate_context(context)


def declared_continuation(descriptor, request, source_context, acknowledged_keys):
    """Add only explicit matching operator declarations to current intended facts.

    Earlier unknown/requested facts cannot be certified. Current established
    conflicts and all device/driver identity declarations are refused. Optical,
    setup, quantity and effective-option acknowledgement is prospective operator
    disclosure, never native readback. The state owner rechecks every originally
    selected reading; this context cannot repair that reading's prior unknowns.
    """
    context = intended(descriptor, request)
    source = validate_context(source_context)
    if source["phase"] != "accepted" or not isinstance(acknowledged_keys, list):
        raise ValueError("Continuation needs an accepted source and explicit acknowledged keys")
    if any(not isinstance(key, str) for key in acknowledged_keys) or len(
        set(acknowledged_keys)
    ) != len(acknowledged_keys):
        raise ValueError("Continuation acknowledgements must name unique material keys")
    for key in acknowledged_keys:
        fact = source["material"].get(key)
        if key in _IDENTITY or key.startswith("native_identity:"):
            raise ValueError(
                "Current device/driver identity cannot be declared from an older source"
            )
        if key not in _DECLARABLE and not key.startswith(("effective_option:", "native_setting:")):
            raise ValueError("Only optical/setup/quantity/effective-setting facts may be declared")
        if (
            fact is None
            or fact["certainty"] in {"unknown", "requested"}
            or not _established(fact["value"])
        ):
            raise ValueError("Earlier unknown/requested material facts cannot be certified")
        current = context["material"].get(key)
        if (
            current is not None
            and current["certainty"] != "unknown"
            and _json(current["value"]) != _json(fact["value"])
        ):
            raise ValueError("Declared continuation conflicts with current intent or descriptor")
        context["material"][key] = _fact(
            fact["value"],
            "operator-declared",
            "Explicit continuation acknowledgement of original accepted fact",
        )
    context["required_material"] = sorted(
        set(context["required_material"]) | set(source["required_material"])
    )
    # Keep missing source-required keys unknown; never silently copy their values.
    for key in context["required_material"]:
        context["material"].setdefault(key, _fact())
    context["provenance"].update(
        continuation_source_context_sha256=_digest(source),
        acknowledged_material=list(acknowledged_keys),
    )
    return validate_context(context)
