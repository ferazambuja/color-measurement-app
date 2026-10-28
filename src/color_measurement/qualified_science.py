"""Explicit derived views over authenticated native inputs and accepted science APIs."""

import math
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from .application import _fields
from .dataset_binding import content_digest
from .illumination import characterized_reflectance, illuminant_spectrum, relight_reflectance
from .models import Spectrum
from .presentation import RESOURCE, corresponding_color
from .reading_dataset import _record_kind, dataset_white_descriptor, resolve_reading
from .science import adapt_xyz, spectral_xyz, xyz_to_lab


def _spectrum(value):
    if value is None:
        return None
    _fields(value, {"wavelengths_nm", "values", "quantity", "unit"})
    return Spectrum(tuple(value["wavelengths_nm"]), tuple(value["values"]),
                    value["quantity"], value["unit"])


def _source_input(row):
    observation = row["observation"]
    return dict(reading_ref=deepcopy(row["reading_ref"]), observation=deepcopy(observation),
                observation_sha256=content_digest(observation))


def _reference_context(readings, context):
    _fields(context, {"reference", "characterization", "characterization_id",
                      "assume_ideal_white", "setup_declaration", "matched_setup",
                      "nonfluorescent_reflectance"})
    for field in ("assume_ideal_white", "matched_setup", "nonfluorescent_reflectance"):
        if type(context[field]) is not bool:
            raise ValueError(f"Declare {field} as an explicit boolean")
    if not isinstance(context["setup_declaration"], str):
        raise ValueError("Reference setup declaration must be text")
    identity = context["characterization_id"]
    if context["assume_ideal_white"]:
        if identity is not None:
            raise ValueError("Ideal-white assumption has no characterization identity")
    elif not isinstance(identity, str) or not identity.strip():
        raise ValueError("Declare the characterization source identity")
    reference = readings[resolve_reading(readings, context["reference"])]
    return reference, _spectrum(context["characterization"])


def _reference_factor(row, reference, characterization, context):
    if not reference["saved"] or _record_kind(reference["observation"]) != "color":
        raise ValueError("Reference requires a saved retained color observation")
    factor = characterized_reflectance(
        _spectrum(row["observation"]["backend_reported"]["spectrum"]),
        _spectrum(reference["observation"]["backend_reported"]["spectrum"]),
        characterization=characterization, assume_ideal_white=context["assume_ideal_white"],
        setup_declaration=context["setup_declaration"], matched_setup=context["matched_setup"],
        nonfluorescent=context["nonfluorescent_reflectance"],
    )
    result = dict(asdict(factor), origin="derived",
                  characterization_id=context["characterization_id"],
                  characterization_sha256=content_digest(context["characterization"])
                  if context["characterization"] is not None else None,
                  source_inputs=dict(sample=_source_input(row), reference=_source_input(reference)))
    return factor, result


def _relighted_result(spectrum, factor_result, row, context, observer_csv, observer_error):
    result = dict(origin="derived", reflectance=asdict(spectrum), reference_factor=factor_result,
                  source_inputs=factor_result["source_inputs"] if factor_result is not None else
                  dict(sample=_source_input(row)), target_illuminant=None,
                  xyz=None, xyz_reason=None, lab=None, lab_reason=None,
                  corresponding_color=None, color_reason=None)
    try:
        if observer_error is not None:
            raise ValueError(observer_error)
        target = context["illuminant"]
        if isinstance(target, str):
            if context["illuminant_id"] is not None:
                raise ValueError("Named CIE illuminant supplies its own authenticated identity")
            declared = illuminant_spectrum(target, spectrum.wavelengths_nm)
            result["target_illuminant"] = dict(asdict(declared), spectrum_sha256=content_digest(
                asdict(declared.spectrum)))
            target, identity = declared.spectrum, declared.source + "; SHA256=" + \
                declared.source_sha256
        else:
            target = _spectrum(target)
            if target is None:
                raise ValueError(
                    "Supply an identified target SPD or an explicit CIE illuminant name",
                )
            identity = context["illuminant_id"]
            result["target_illuminant"] = dict(
                kind="caller-declared", identity=identity, spectrum=asdict(target),
                spectrum_sha256=content_digest(context["illuminant"]),
            )
        computed = relight_reflectance(
            spectrum, illuminant=target, illuminant_id=identity,
            observer=context["observer"], observer_csv=observer_csv,
            wavelength_convention=context["wavelength_convention"],
            nonfluorescent=context["nonfluorescent_reflectance"],
        )
        result["xyz"] = dict(asdict(computed), origin="derived")
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        result["xyz_reason"] = str(error)
        result["lab_reason"] = result["color_reason"] = "Relighted XYZ is unavailable"
        return result
    try:
        result["lab"] = xyz_to_lab(computed.values, computed.reference_white)
    except (ValueError, TypeError, OverflowError) as error:
        result["lab_reason"] = str(error)
    if context["color_space"] is None:
        result["color_reason"] = "Choose an explicit corresponding-color display encoding"
    else:
        try:
            result["corresponding_color"] = asdict(corresponding_color(
                computed.values, reference_white=computed.reference_white,
                observer=computed.observer, color_space=context["color_space"],
            ))
        except (ValueError, TypeError, OverflowError) as error:
            result["color_reason"] = str(error)
    return result


def derive_readings(readings, selected, request):
    """Compose separate corresponding-color or spectral XYZ results; never enrich records.

    Bradford's normalized whites preserve the native sample scale. The Lab
    reference is destination_white/destination_white_Y*source_white_Y, using
    the library's declared convention. Spectral integration retains the original
    grid and uses only explicit observer, wavelength medium and optional SPD.
    Equations, primary sources and numerical limits remain at the science owner.
    """
    _fields(request, {"method", "context"})
    method, context = request["method"], request["context"]
    if method == "adapt_xyz":
        _fields(context, {"source_white", "destination_white"})
        source = dataset_white_descriptor(readings, context["source_white"])
        destination = dataset_white_descriptor(readings, context["destination_white"])
        if source is None or destination is None:
            raise ValueError("Adaptation requires two explicit adopted whites")
        if any(source[key] != destination[key] for key in ("unit", "scale", "observer")):
            raise ValueError("Adopted-white observer, unit and scale must match")
        lab_white = tuple(value / destination["reference_y"] * source["reference_y"]
                          for value in destination["xyz"])
        lab_white_reason = None
        if any(not math.isfinite(value) or value <= 0 for value in lab_white):
            lab_white = None
            lab_white_reason = (
                "Normalized destination Lab white is not finite and strictly positive"
            )
    elif method == "spectral_xyz":
        _fields(context, {"observer", "wavelength_convention", "illuminant", "illuminant_id",
                          "nonfluorescent_reflectance"})
        if type(context["nonfluorescent_reflectance"]) is not bool:
            raise ValueError("Declare the nonfluorescent-reflectance assumption explicitly")
        illuminant = _spectrum(context["illuminant"])
    elif method == "characterized_reflectance":
        reference, characterization = _reference_context(readings, context)
    elif method == "relight_reflectance":
        _fields(context, {"reflectance", "illuminant", "illuminant_id", "observer",
                          "wavelength_convention", "nonfluorescent_reflectance", "color_space"})
        if type(context["nonfluorescent_reflectance"]) is not bool:
            raise ValueError("Declare the nonfluorescent-reflectance assumption explicitly")
        if any(not isinstance(context[field], str)
               for field in ("observer", "wavelength_convention")):
            raise ValueError("Declare observer and wavelength convention as text")
        if context["color_space"] is not None and not isinstance(context["color_space"], str):
            raise ValueError("Corresponding-color encoding must be text or null")
        if context["illuminant_id"] is not None and not isinstance(context["illuminant_id"], str):
            raise ValueError("Illuminant identity must be text or null")
        if not isinstance(context["illuminant"], (str, dict)):
            raise ValueError("Declare a CIE illuminant name or a typed target SPD")
        basis = context["reflectance"]
        if (not isinstance(basis, dict)
                or basis.get("kind") not in {"native", "characterized-reference"}):
            raise ValueError("Choose native reflectance or an explicit characterized reference")
        _fields(basis, {"kind"} if basis["kind"] == "native" else {"kind", "context"})
        if basis["kind"] == "characterized-reference":
            reference, characterization = _reference_context(readings, basis["context"])
    else:
        raise ValueError(
            "Choose adapt_xyz, spectral_xyz, characterized_reflectance or relight_reflectance",
        )
    if method in {"spectral_xyz", "relight_reflectance"}:
        observer_error = None
        try:
            observer_csv = RESOURCE.read_bytes()
        except OSError as error:
            observer_csv, observer_error = None, f"Observer resource is unavailable: {error}"
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(key, str) or key not in readings for key in selected)
            or len(set(selected)) != len(selected)):
        raise ValueError("Choose unique retained readings for derivation")
    output = {}
    for key in selected:
        row = readings[key]
        observation = row["observation"]
        result, reason = None, None
        try:
            if not row["saved"]:
                raise ValueError("Save the input before deriving a source-bound result")
            if _record_kind(observation) != "color":
                raise ValueError("Color derivation requires a retained color observation")
            if method == "adapt_xyz":
                native = observation["backend_reported"]["xyz"]
                if native is None:
                    raise ValueError("Native XYZ is unavailable")
                if any(native[field] != source[field] for field in ("unit", "scale", "observer")):
                    raise ValueError("Native XYZ and adopted-white observer/unit/scale differ")
                if source["kind"] == "retained-reading" and native["illuminant"] != \
                        source["illuminant"]:
                    raise ValueError("Native XYZ and retained source-white conditions differ")
                values = adapt_xyz(native["values"], source["xyz"], destination["xyz"])
                result = dict(
                    values=values, origin="derived", unit=native["unit"], scale=native["scale"],
                    observer=native["observer"], source_white=source, destination_white=destination,
                    lab_white=lab_white, lab_white_reason=lab_white_reason,
                    lab=None, lab_reason=lab_white_reason,
                    algorithm="bradford-full-linear", algorithm_version="1",
                    prediction="Corresponding colors; no spectral relighting or native replacement",
                )
                if lab_white is not None:
                    try:
                        result["lab"] = xyz_to_lab(values, lab_white)
                    except (ValueError, TypeError, OverflowError) as error:
                        result["lab_reason"] = str(error)
            elif method == "spectral_xyz":
                if observer_error is not None:
                    raise ValueError(observer_error)
                spectrum = _spectrum(observation["backend_reported"]["spectrum"])
                if spectrum is not None and spectrum.quantity == "reflectance" and not \
                        context["nonfluorescent_reflectance"]:
                    raise ValueError("Declare the nonfluorescent-reflectance model for integration")
                computed = spectral_xyz(
                    spectrum, observer=context["observer"], observer_csv=observer_csv,
                    wavelength_convention=context["wavelength_convention"], illuminant=illuminant,
                    illuminant_id=context["illuminant_id"],
                )
                result = dict(asdict(computed), origin="derived")
            elif method == "characterized_reflectance":
                _, result = _reference_factor(row, reference, characterization, context)
            else:
                factor_result = None
                if basis["kind"] == "characterized-reference":
                    factor, factor_result = _reference_factor(
                        row, reference, characterization, basis["context"],
                    )
                    spectrum = factor.spectrum
                else:
                    spectrum = _spectrum(observation["backend_reported"]["spectrum"])
                    if spectrum is None or spectrum.quantity != "reflectance":
                        raise ValueError(
                            "Native relighting requires retained reflectance, not radiance",
                        )
                result = _relighted_result(
                    spectrum, factor_result, row, context, observer_csv, observer_error,
                )
            result["source_observation_sha256"] = content_digest(observation)
            result["input_quantity_basis"] = dict(observation["configuration"]).get(
                "units_source", "Retained backend-reported quantity declaration",
            )
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            reason = str(error)
        output[key] = dict(result=result, reason=reason)
    return dict(
        method=method, request=request, context_sha256=content_digest(request), by_source=output,
        implementation_sha256=content_digest(dict(
            consumer=Path(__file__).with_suffix(".py").read_text(encoding="utf-8"),
            science=Path(__file__).with_name("science.py").read_text(encoding="utf-8"),
            illumination_owner=Path(__file__).with_name("illumination.py").read_text(encoding="utf-8"),
            white_owner=Path(__file__).with_name("presentation.py").read_text(encoding="utf-8"),
            dataset_owner=Path(__file__).with_name("reading_dataset.py").read_text(encoding="utf-8"),
        )),
        limitations="Explicit numerical assumptions do not establish physical validity or accuracy",
    )
