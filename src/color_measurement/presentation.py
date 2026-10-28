"""Illustrative display DTOs composed from the shared Python science owner."""

import base64
import csv
import hashlib
import math
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from color_measurement.science import (
    ColorDomainError,
    adapt_xyz,
    rgb_gamut_status,
    rgb_reference_geometry,
    standard_reference_white,
    uvy_to_xyz,
    xyy_to_xyz,
    xyz_to_lab,
    xyz_to_rgb,
    xyz_to_uvy,
    xyz_to_xyy,
)

from .argyll_capability import parse_mode_name, route_illumination
from .illumination import illuminant_white
from .models import Observation, observation_from_dict

TARGETS = ("srgb", "display-p3")
TARGET_NAMES = {"srgb": "sRGB", "display-p3": "Display P3"}
RESOURCE = (
    Path(__file__).resolve().parent / "desktop_assets" / "resources" / "CIE_xyz_1931_2deg.csv"
)
CMF_SHA256 = "fa663e3535a7e0763a745993a1f0a192eb0275ac46ad2d1befd7626841e713c1"
#: Common luminance, in cd/m², of the dataset previews and the bundled reference
#: gamuts until the operator applies another scale. The desktop session and the
#: asset generator read it; the renderer takes it from their replies and never
#: restates the number.
DEFAULT_PREVIEW_SCALE = 100
RELATIVE_SCALE = "relative; perfect-diffuser Y=100"


@lru_cache(maxsize=12)
def _reflective_white(name, observer_csv):
    return illuminant_white(
        name, observer="CIE1931_2", reference_y=100, unit="1", scale=RELATIVE_SCALE,
        observer_csv=observer_csv,
    )


def qualify_reflective_xyz(observation, *, observer_csv=None):
    """Qualify original relative reflective XYZ and its declared theoretical white.

    Reuses the Argyll mode grammar and illumination.illuminant_white (CIE 1 nm
    SPD/CMF sum, 360..830 nm, common Y=100). No sample normalization or source
    white guessing occurs. Native units/scale/observer/illuminant must declare
    unit 1, perfect-diffuser Y=100, CIE1931_2 and D50/D65/A respectively.
    Chart callers separately authenticate accepted context and reading identity;
    ordinary dataset callers supply their authenticated original observations.

    Example: a retained black (0,0,0) is available without invented chromaticity;
    a missing XYZ is unavailable even if computed values or spectra exist.
    This validates an illustrative model's input, never calibration, physical
    appearance, placement or native D65 measurements. Scientific equations and
    source attribution remain with the illumination and corresponding-color owners.
    """
    result = dict(available=False, reason="", xyz=None, context=None)
    if not isinstance(observation, Observation):
        result["reason"] = "Relative preview requires an original color observation"
        return result
    native = observation.backend_reported.xyz
    if native is None:
        result["reason"] = "Native XYZ is unavailable; no replacement is inferred"
        return result
    try:
        reflective = route_illumination(parse_mode_name(observation.actual_mode)[0]) in {
            "reflection", "s_reflection",
        }
    except ValueError:
        reflective = False
    if (observation.device.backend not in {"argyll-native", "argyll-demo"} or not reflective
            or observation.device.backend == "argyll-demo"
            and observation.provenance != "simulated"):
        result["reason"] = "Relative previews require retained Argyll reflective acquisition"
    elif (native.unit != "1" or native.scale != RELATIVE_SCALE
          or native.observer != "CIE1931_2" or native.illuminant not in {"D50", "D65", "A"}):
        result["reason"] = "Relative preview needs CIE1931_2 unit-one Y=100 XYZ under D50, D65 or A"
    elif any(value < 0 for value in native.values):
        result["reason"] = "Relative reflective XYZ must be finite and nonnegative"
    else:
        try:
            white = _reflective_white(
                native.illuminant, RESOURCE.read_bytes() if observer_csv is None else observer_csv,
            )
            result.update(available=True, xyz=list(native.values), context=dict(
                unit=native.unit, scale=native.scale, observer=native.observer,
                illuminant=native.illuminant, white=asdict(white),
            ))
        except (OSError, ValueError, OverflowError, TypeError) as error:
            result["reason"] = f"Relative reflective calculation unavailable: {error}"
    return result


@dataclass(frozen=True)
class CorrespondingColor:
    adapted_xyz: tuple[float, float, float]
    linear_rgb: tuple[float, float, float]
    display_rgb: tuple[float, float, float]
    clipped_channels: tuple[bool, bool, bool]
    source_white: tuple[float, float, float]
    destination_white: tuple[float, float, float]
    observer: str
    color_space: str
    common_reference_y: float
    algorithm: str = "bradford-corresponding-color-display"
    algorithm_version: str = "2"


def corresponding_color(xyz, *, reference_white, observer, color_space) -> CorrespondingColor:
    """Project explicit-white XYZ to an ideal D65 display at one common white Y.

    Adapt XYZ with full Bradford from the supplied white to the RGB matrix
    owner's nominal D65; encode adapted_xyz/white_Y using shared xyz_to_rgb.
    Only whites are normalized inside adaptation. All samples share the same
    white_Y, never their own brightness. Source: ICC.1:2004-10 Annex E.3 and
    CSS Color 4 section 19, https://www.color.org/ICC1V42.pdf and
    https://www.w3.org/TR/2026/CRD-css-color-4-20261009/#color-conversion-code

    Example: XYZ equal to the supplied white produces approximately RGB=(1,1,1);
    black produces (0,0,0). Input XYZ/white must have compatible units/scale and
    CIE1931_2 observer. The scalar triples cannot establish those facts: caller
    validates retained relative/derived context before invoking this method.

    Signed linear channels and individual clipping flags remain separate from
    the bounded illustrative display RGB. For a validated sample exactly equal
    to its declared white, the mathematical display endpoint is (1,1,1).
    Version 2 suppresses only positive upper rounding excursions of at most
    16 binary64 ulps at 1 in this endpoint's illustrative clipping flags.
    Adapted XYZ, signed channels and bounded display RGB remain unchanged;
    every other sample and the direct RGB owner keep their strict flags.
    This numerical-only bound is not a measurement uncertainty or gamut tolerance.
    This is a declared corresponding-color
    model, not native preview replacement, spectral relighting, profile correction,
    calibrated screen color, viewing-condition prediction or physical validation.
    No input record is modified. Domain/range refusal raises ColorDomainError.
    """
    if observer != "CIE1931_2":
        raise ColorDomainError("corresponding display color requires CIE1931_2")
    destination = standard_reference_white(
        "D65", observer=observer, reference_y=1, unit="1", scale="RGB reference Y=1"
    ).xyz
    # Shared adaptation owner validates positive finite white and sample floats.
    adapted = adapt_xyz(xyz, reference_white, destination)
    white = tuple(float(value) for value in reference_white)
    encoded = xyz_to_rgb(adapted, color_space=color_space, reference_luminance=white[1])
    clipped = encoded.clipped_channels
    if tuple(float(value) for value in xyz) == white:
        upper = 1.0 + 16 * math.ulp(1.0)
        clipped = tuple(flag and not 1.0 < value <= upper
                        for flag, value in zip(clipped, encoded.linear_rgb))
    return CorrespondingColor(
        adapted, encoded.linear_rgb, encoded.display_rgb, clipped,
        white, destination, observer, color_space, white[1],
    )


def encoded_targets(xyz, scale):
    """Use the established encoder; rendering colors do not become observations."""
    return {
        target: xyz_to_rgb(xyz, color_space=target, reference_luminance=scale).display_rgb
        for target in TARGETS
    }


def display_projections(readings, scale):
    previews = {}
    for key, row in readings.items():
        native = row["observation"]["backend_reported"]["xyz"]
        eligible = (
            native is not None
            and native["observer"] == "CIE1931_2"
            and native["unit"] == "cd/m^2"
            and native["scale"] == "absolute; Y is luminance"
            and native["illuminant"] == "none (emissive)"
        )
        relative = None
        if not eligible and native is not None:
            try:
                relative = qualify_reflective_xyz(observation_from_dict(row["observation"]))
            except (TypeError, ValueError, OverflowError) as error:
                relative = dict(available=False, reason=str(error))
        previews[key] = {}
        for target in TARGETS:
            if relative is not None and relative["available"]:
                context = relative["context"]
                try:
                    encoding = corresponding_color(
                        relative["xyz"], reference_white=context["white"]["xyz"],
                        observer=context["observer"], color_space=target,
                    )
                except (TypeError, ValueError, OverflowError) as error:
                    previews[key][target] = dict(display_rgb=None, gamut_status=None,
                                                 reason=str(error))
                    continue
                previews[key][target] = dict(
                    linear_rgb=encoding.linear_rgb, display_rgb=encoding.display_rgb,
                    clipped_channels=encoding.clipped_channels,
                    common_reference_y=encoding.common_reference_y, reference_unit=context["unit"],
                    reference_scale=context["scale"], algorithm=encoding.algorithm,
                    algorithm_version=encoding.algorithm_version,
                    mapping="full Bradford to nominal D65; common relative white Y=100; "
                            "display clamp",
                    source_white=context["white"],
                    native_context={name: value for name, value in context.items()
                                    if name != "white"},
                    destination_white=encoding.destination_white, adapted_xyz=encoding.adapted_xyz,
                    gamut_status=None,
                )
                continue
            if not eligible:
                previews[key][target] = {
                    "display_rgb": None,
                    "gamut_status": None,
                    "reason": "Native XYZ is unavailable." if native is None else
                    relative["reason"] if relative is not None else
                    "Preview requires qualified native XYZ context.",
                }
                continue
            try:
                status = rgb_gamut_status(
                    native["values"], color_space=target, reference_luminance=scale
                )
            except (TypeError, ValueError, OverflowError) as error:
                previews[key][target] = {
                    "display_rgb": None,
                    "gamut_status": None,
                    "reason": str(error),
                }
                continue
            encoding = status.encoding
            coordinates = {"xyy": status.xy, "uvy": status.uv}
            previews[key][target] = {
                "linear_rgb": encoding.linear_rgb,
                "display_rgb": encoding.display_rgb,
                "clipped_channels": encoding.clipped_channels,
                "reference_luminance": scale,
                "reference_unit": "cd/m^2",
                "algorithm": status.algorithm,
                "algorithm_version": status.algorithm_version,
                "mapping": "per-channel clamp; no chromatic adaptation or tone mapping",
                "gamut_status": {
                    "below_zero": [i for i, flag in enumerate(status.below_zero_channels) if flag],
                    "above_one": [i for i, flag in enumerate(status.above_one_channels) if flag],
                    "outside_volume": not status.within_rgb_cube,
                    "numerical_state": status.numerical_state,
                    "numerical_error_bounds": status.numerical_error_bounds,
                    "max_lower_excess": status.max_lower_excess,
                    "max_upper_excess": status.max_upper_excess,
                    "required_positive_rgb_scale": status.required_positive_rgb_scale,
                    "fit_positive_rgb_scale": status.fit_positive_rgb_scale,
                    "fit_scale_reason": status.fit_scale_reason,
                    "positive_stimulus_eligible": status.positive_stimulus_eligible,
                    "chromaticity": {
                        name: {
                            "coordinates": value.coordinates,
                            "outside_triangle": None
                            if value.in_triangle is None
                            else not value.in_triangle,
                            "reason": value.unavailable_reason,
                            "numerical_state": value.numerical_state,
                            "numerical_error_bounds": value.numerical_error_bounds,
                        }
                        for name, value in coordinates.items()
                    },
                }
                if eligible
                else None,
            }
    return previews


@lru_cache(maxsize=1)
def cie_cmf_rows():
    """Authenticate the immutable published CIE 1931 2° / 1 nm resource once."""
    raw = RESOURCE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != CMF_SHA256:
        raise ValueError("The official CIE CMF resource bytes changed.")
    rows = [tuple(float(value) for value in row) for row in csv.reader(raw.decode().splitlines())]
    if len(rows) != 471 or any(len(row) != 4 or row[0] != 360 + i for i, row in enumerate(rows)):
        raise ValueError("Require the published 360–830 nm / 1 nm table.")
    return tuple(rows)


@lru_cache(maxsize=2)
def _illustration_y_weights(target):
    """Reuse the shared CSS target's primary Y coefficients at unit scale."""
    geometry = rgb_reference_geometry(
        color_space=target, reference_luminance=1.0, reference_white=None, samples_per_edge=2
    )
    return tuple(primary[1] for primary in geometry.primaries_xyz)


def illustrative_targets(xyz):
    """Dim only the chromaticity illustration to relative output Y <= 0.25.

    c=M^-1*XYZ, p=max(c,0), Y+=sum(primary_Y*p), S=max(max(p),Y+/0.25).
    Reuse the shared XYZ encoder at S. Positive-channel Y includes the effect
    of negative-channel clamping; using original XYZ Y would miss that effect.
    Signed channels remain signed until the display output clamps them. This
    changes illustrative brightness, never a native reading or physical spectral
    appearance. The cap is ideal relative Y before RGBA8 quantization and
    browser scaling, with binary64 roundoff; it is not a physical luminance.
    Fixed raster inputs remain finite; existing numerical domain limits apply.
    CSS Color 4 defines the matrices/transfer; CIE's CMFs define
    the wavelength/chromaticity inputs. No private color kernel is introduced.
    """
    result = {}
    for target in TARGETS:
        raw = xyz_to_rgb(xyz, color_space=target, reference_luminance=1.0)
        positive = tuple(max(0.0, channel) for channel in raw.linear_rgb)
        luminance = math.fsum(
            weight * channel for weight, channel in zip(_illustration_y_weights(target), positive)
        )
        scale = max(*positive, luminance / 0.25)
        if scale == 0.0:
            raise ValueError("An illustration requires a positive RGB channel.")
        encoded = xyz_to_rgb(xyz, color_space=target, reference_luminance=scale)
        result[target] = {
            "display_rgb": encoded.display_rgb,
            "normalization_scale": scale,
            "negative_channels": tuple(
                i for i, value in enumerate(encoded.linear_rgb) if value < 0
            ),
            "upper_channels": tuple(i for i, value in enumerate(encoded.linear_rgb) if value > 1),
        }
    return result


@lru_cache(maxsize=1)
def wavelength_guide():
    """Label wavelengths with common-scale CMF colors, not measured intensity."""
    samples = []
    for row in cie_cmf_rows()[::5]:
        targets = {
            target: xyz_to_rgb(row[1:], color_space=target, reference_luminance=1.0)
            for target in TARGETS
        }
        samples.append(
            {
                "wavelength_nm": row[0],
                "xyz": row[1:],
                "colors": {target: value.display_rgb for target, value in targets.items()},
                "negative_channels": {
                    target: tuple(i for i, channel in enumerate(value.linear_rgb) if channel < 0)
                    for target, value in targets.items()
                },
                "upper_channels": {
                    target: tuple(i for i, channel in enumerate(value.linear_rgb) if channel > 1)
                    for target, value in targets.items()
                },
            }
        )
    return {
        "observer": "CIE1931_2",
        "source_doi": "10.25039/CIE.DS.xvudnb9b",
        "resource_sha256": CMF_SHA256,
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "unit": "relative XYZ / dimensionless CMF",
        "reference_scale": 1.0,
        "recipe": (
            "XYZ=(x_bar,y_bar,z_bar); one common relative scale 1; "
            "signed channel clamp; no per-band normalization"
        ),
        "samples": samples,
        "scope": (
            "Wavelength identification only; not measured amplitude, "
            "physical spectral appearance or a per-reading scale"
        ),
    }


@lru_cache(maxsize=1)
def spectral_locus():
    """Project original 1 nm CMF triples with the existing coordinate owners."""
    samples = [
        {"wavelength_nm": row[0], "xyy": xyz_to_xyy(row[1:])[:2], "uvy": xyz_to_uvy(row[1:])[:2]}
        for row in cie_cmf_rows()
    ]
    return {
        "observer": "CIE1931_2",
        "source_doi": "10.25039/CIE.DS.xvudnb9b",
        "resource_sha256": CMF_SHA256,
        "license": "CC BY-SA 4.0",
        "samples": samples,
        "wavelength_range_nm": (360, 830),
        "step_nm": 1,
        "purple_boundary_indices": (0, len(samples) - 1),
        "coordinate_unit": "1",
        "scope": (
            "Published monochromatic reference chromaticities; "
            "not a display gamut or measured coverage"
        ),
    }


@lru_cache(maxsize=1)
def chromaticity_field():
    """Evaluate a regular pixel-center field with the existing Python owners.

    Each view inverts its own dimensionless coordinates at illustrative Y=1.
    The complete original locus, closed with the purple line, supplies even-odd
    scanline intervals. Outside pixels are transparent. Each admitted point
    uses illustrative_targets; encoded channels round to RGBA8. Canvas scaling
    and quantization remain presentation approximations, not native data,
    physical spectral appearance, luminance or measured coverage.
    """
    size = 384
    locus = spectral_locus()["samples"]
    views = {}
    for view, inverse in (("xyy", xyy_to_xyz), ("uvy", uvy_to_xyz)):
        boundary = tuple(sample[view] for sample in locus)
        # A small margin keeps the authenticated edge within the raster.
        x0 = min(p[0] for p in boundary) - 0.002
        x1 = max(p[0] for p in boundary) + 0.002
        y0 = min(p[1] for p in boundary) - 0.002
        y1 = max(p[1] for p in boundary) + 0.002
        buffers = {target: bytearray(size * size * 4) for target in TARGETS}
        valid_pixels = 0
        for row in range(size):
            y = y1 - (row + 0.5) / size * (y1 - y0)
            intersections = []
            for a, b in zip(boundary, (*boundary[1:], boundary[0])):
                if (a[1] > y) != (b[1] > y):
                    intersections.append(a[0] + (y - a[1]) / (b[1] - a[1]) * (b[0] - a[0]))
            intersections.sort()
            if len(intersections) % 2:
                raise ValueError("Locus scanline intersections must form closed intervals.")
            for col in range(size):
                x = x0 + (col + 0.5) / size * (x1 - x0)
                if not any(a <= x < b for a, b in zip(intersections[::2], intersections[1::2])):
                    continue
                xyz = inverse((x, y, 1.0))
                targets = illustrative_targets(xyz)
                offset = (row * size + col) * 4
                for target, value in targets.items():
                    channels = bytes(round(c * 255) for c in value["display_rgb"])
                    buffers[target][offset : offset + 4] = channels + b"\xff"
                valid_pixels += 1
        views[view] = {
            "width": size,
            "height": size,
            "extent": (x0, x1, y0, y1),
            "row_order": "top-to-bottom; y decreases",
            "valid_pixels": valid_pixels,
            "rgba8": {
                target: base64.b64encode(raw).decode("ascii") for target, raw in buffers.items()
            },
        }
    return {
        "views": views,
        "observer": "CIE1931_2",
        "source_doi": "10.25039/CIE.DS.xvudnb9b",
        "resource_sha256": CMF_SHA256,
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "coordinate_unit": "1",
        "authoritative_boundary_step_nm": 1,
        "relative_y_cap": 0.25,
        "recipe": ("per-pixel Python coordinate inverse; relative output Y cap 0.25 "
                   "after negative-channel clamp; encoded RGBA8; full locus clip"),
        "scope": ("Approximate subdued chromaticity illustration, not physical spectral "
                  "appearance, measured coverage or per-reading normalization"),
    }


def resolve_white(readings, request):
    """Resolve a current declaration without manufacturing native observations.

    Retained references preserve the native four-field context. Theoretical
    references use the shared nominal catalogue with explicit Y/unit/scale/
    observer. Their direct Lab assumption does not infer an illuminant, adapt
    native XYZ or integrate an SPD. No declaration is selected by default.
    """
    if request is None:
        return None
    if not isinstance(request, dict):
        raise ValueError("White declaration must be an object or null.")
    if request.get("kind") == "retained-reading":
        if set(request) != {"kind", "source_id"} or request["source_id"] not in readings:
            raise ValueError("Choose one retained white identity.")
        source_id = request["source_id"]
        native = readings[source_id]["observation"]["backend_reported"]["xyz"]
        if native is None:
            raise ValueError("Declared white source has no native XYZ.")
        xyz = tuple(native["values"])
        if any(v <= 0 for v in xyz):
            raise ValueError("Declared white must have positive XYZ.")
        return {
            "kind": "retained-reading",
            "source_id": source_id,
            "xyz": xyz,
            "reference_y": xyz[1],
            **{k: native[k] for k in ("unit", "scale", "observer", "illuminant")},
        }
    if request.get("kind") == "theoretical-lab-reference":
        fields = {"kind", "name", "observer", "reference_y", "unit", "scale"}
        if set(request) != fields:
            raise ValueError("Standard white needs explicit name, observer, Y, unit and scale.")
        return asdict(standard_reference_white(**{k: request[k] for k in fields - {"kind"}}))
    raise ValueError("Unsupported white kind.")


def lab_projections(readings, white):
    """Compose Lab only for matching declaration context; keep absence explicit."""
    values, reasons = {}, {}
    if white is None:
        return values, reasons
    keys = ("unit", "scale", "observer")
    if white["kind"] == "retained-reading":
        keys += ("illuminant",)
    for key, row in readings.items():
        native = row["observation"]["backend_reported"]["xyz"]
        if native is None:
            reasons[key] = "Native XYZ is unavailable."
            continue
        if any(native[k] != white[k] for k in keys):
            reasons[key] = "Reading and declared-white units/scale/observer/conditions differ."
            continue
        try:
            values[key] = xyz_to_lab(native["values"], white["xyz"])
        except (TypeError, ValueError, OverflowError) as error:
            reasons[key] = str(error)
    return values, reasons


def reference_projections(scale, white=None, *, readings=None):
    """Keep ideal components and their native-context eligibility independent.

    With no readings argument this is a standalone reference illustration.
    A bound dataset never borrows the model's absolute cd/m² scale or observer.
    """
    contexts = []
    if readings is not None:
        for row in readings.values():
            native = row["observation"]["backend_reported"]["xyz"]
            if native is not None:
                context = {key: native[key] for key in ("unit", "scale", "observer", "illuminant")}
                if context not in contexts:
                    contexts.append(context)
    xyz_reason, chromaticity_reason = None, None
    if readings:
        if not contexts:
            xyz_reason = "Native XYZ is unavailable for comparison with the reference model."
            chromaticity_reason = "No native XYZ observer is available for reference comparison."
        else:
            if any(context != dict(
                unit="cd/m^2", scale="absolute; Y is luminance",
                observer="CIE1931_2", illuminant="none (emissive)",
            ) for context in contexts):
                xyz_reason = (
                    "Native XYZ context does not match the absolute emissive CIE1931_2 RGB model."
                )
            if any(context["observer"] != "CIE1931_2" for context in contexts):
                chromaticity_reason = (
                    "Native observer does not match the CIE1931_2 reference model."
                )
    result = {}
    for target in TARGETS:
        reasons = {}
        if xyz_reason is not None:
            reasons["xyz"] = xyz_reason
        if chromaticity_reason is not None:
            reasons["chromaticity"] = chromaticity_reason
        try:
            geometry = rgb_reference_geometry(
                color_space=target,
                reference_luminance=scale,
                reference_white=None,
                samples_per_edge=9,
            )
        except (TypeError, ValueError, OverflowError) as error:
            result[target] = {
                "name": TARGET_NAMES[target],
                "primaries": None,
                "xyz_points": None,
                "lab_points": None,
                "vertex_colors": {},
                "lines": [],
                "triangles": [],
                "component_reasons": {"geometry": str(error)},
            }
            continue
        xyz = [point.xyz for point in geometry.surface]
        lab = None
        if white is not None and xyz_reason is not None:
            reasons["lab"] = xyz_reason
        elif white is not None:
            compatible = (
                white["observer"] == "CIE1931_2"
                and white["unit"] == "cd/m^2"
                and white["scale"] == "absolute; Y is luminance"
            )
            if not compatible:
                reasons["lab"] = "Declared white and RGB model units/scale/observer differ."
            else:
                try:
                    lab = [xyz_to_lab(point, white["xyz"]) for point in xyz]
                except (TypeError, ValueError, OverflowError) as error:
                    reasons["lab"] = str(error)
        vertex_colors = {}
        try:
            encoded = [encoded_targets(point, scale) for point in xyz]
            vertex_colors = {
                encoding: [value[encoding] for value in encoded] for encoding in TARGETS
            }
        except (TypeError, ValueError, OverflowError) as error:
            reasons["surface_colors"] = str(error)
        result[target] = {
            "name": TARGET_NAMES[target],
            "observer": "CIE1931_2",
            "white": "D65 target",
            "reference_luminance": scale,
            "reference_unit": "cd/m^2",
            "reference_scale": "absolute; Y is luminance",
            "reference_illuminant": "none (emissive)",
            "native_contexts": contexts,
            "black": "zero",
            "primaries": {"xyy": geometry.primaries_xy, "uvy": geometry.primaries_uv}
            if chromaticity_reason is None else None,
            "white_point": {"xyy": geometry.white_xy, "uvy": geometry.white_uv}
            if chromaticity_reason is None else None,
            "xyz_points": xyz if xyz_reason is None else None,
            "lab_points": lab,
            "lines": [
                edge
                for edge in geometry.lines
                if sum(
                    geometry.surface[edge[0]].linear_rgb[axis]
                    == geometry.surface[edge[1]].linear_rgb[axis]
                    and geometry.surface[edge[0]].linear_rgb[axis] in (0.0, 1.0)
                    for axis in range(3)
                )
                == 2
            ],
            "triangles": geometry.triangles,
            "vertex_colors": vertex_colors,
            "component_reasons": reasons,
            "algorithm": geometry.algorithm,
            "algorithm_version": geometry.algorithm_version,
            "samples_per_edge": geometry.samples_per_edge,
            "convention": "Ideal additive RGB reference; not measured device coverage, ICC or HDR",
        }
    return result
