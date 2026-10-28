"""Typed strip operands published by the native driver policy, without model tables.

These constraints describe what the pinned driver consumes, not physical target
recognition. Defaults are advertised separately from values the caller supplied.
"""

import math
from copy import deepcopy

from .argyll_capability import decode_facilities, parse_mode_name, route_illumination


def validate_policies(value: dict | None) -> dict:
    """Validate native facts before publishing a consumer contract."""
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) != {"reflection", "transmission", "emission"}:
        raise ValueError("Invalid native strip policies")
    for policy in value.values():
        if (not isinstance(policy, dict)
                or set(policy) != {"max_patches", "requirement", "layout_fields"}
                or type(policy["max_patches"]) is not int
                or not 0 <= policy["max_patches"] <= 4096
                or policy["requirement"] is not None and not isinstance(policy["requirement"], str)
                or not isinstance(policy["layout_fields"], list)):
            raise ValueError("Invalid native strip operand policy")
        seen = set()
        for field in policy["layout_fields"]:
            if (not isinstance(field, dict) or not isinstance(field.get("key"), str)
                    or field.get("type") not in ("integer", "number", "text")):
                raise ValueError("Invalid strip field")
            base = {"key", "label", "type", "required", "default", "help"}
            extra = ({"minimum", "maximum", "exclusive_minimum", "unit"}
                     if field.get("type") in {"integer", "number"}
                     else {"max_bytes", "encoding"})
            if (set(field) != base | extra
                    or field["key"] not in {"strip_name", "pass_name", "guide", "patch_width_mm",
                                             "gap_width_mm", "trailer_width_mm"}
                    or field["key"] in seen
                    or not all(isinstance(field[k], str) and field[k]
                               for k in ("key", "label", "help"))
                    or type(field["required"]) is not bool):
                raise ValueError("Invalid or duplicate strip field")
            seen.add(field["key"])
            if field["type"] in {"integer", "number"}:
                if (not _finite(field["minimum"]) or field["minimum"] < 0
                        or field["maximum"] is not None and (
                            not _finite(field["maximum"]) or field["maximum"] < field["minimum"])
                        or type(field["exclusive_minimum"]) is not bool
                        or not isinstance(field["unit"], str)):
                    raise ValueError("Invalid strip numeric bounds")
            elif (field["type"] != "text" or field["encoding"] != "ascii_printable"
                    or type(field["max_bytes"]) is not int or not 1 <= field["max_bytes"] <= 128):
                raise ValueError("Invalid strip text bounds")
            if field["required"] and field["default"] is not None:
                raise ValueError("Required strip fields cannot have guessed defaults")
            if field["default"] is not None:
                _validate_value(field, field["default"])
    return deepcopy(value)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _validate_value(field, value):
    """Validate the common typed-field vocabulary without filling omitted values."""
    key, kind = field["key"], field["type"]
    if kind in {"integer", "number"}:
        if (not _finite(value) or kind == "integer" and type(value) is not int
                or value < field["minimum"]
                or field["exclusive_minimum"] and value == field["minimum"]
                or field["maximum"] is not None and value > field["maximum"]):
            raise ValueError(f"{key} is outside its published numeric bounds")
    elif kind == "text":
        encoding = field["encoding"]
        if (not isinstance(value, str) or not value
                or len(value.encode("utf-8")) > field["max_bytes"]
                or encoding == "ascii_printable" and any(not 32 <= ord(c) <= 126 for c in value)
                or encoding == "utf8" and not value.strip()):
            label = "printable ASCII" if encoding == "ascii_printable" else "nonblank UTF-8"
            raise ValueError(f"{key} requires bounded {label} text")
    elif kind == "choice":
        if value not in field["choices"]:
            raise ValueError(f"{key} is not an offered choice")
    elif kind == "boolean":
        if type(value) is not bool:
            raise ValueError(f"{key} must be an explicit boolean")
    elif kind == "integer_array":
        if (not isinstance(value, (list, tuple))
                or not field["min_items"] <= len(value) <= field["max_items"]):
            raise ValueError(f"{key} requires a bounded integer array")
        item = dict(field, type="integer")
        for number in value:
            _validate_value(item, number)
    else:
        raise ValueError(f"Unknown layout field type: {kind}")


def workflow_fields(policies, mode, secondary):
    """Return one assessed workflow's metadata; None means unassessed."""
    route, _, _ = parse_mode_name(mode)
    policy = policies.get(route_illumination(route))
    if not policy or not policy["max_patches"]:
        return None
    fields = deepcopy(policy["layout_fields"])
    facilities = set(decode_facilities(secondary))
    fields.append({"key": "direction", "label": "Returned patch order", "type": "choice",
                   "required": False, "default": "forward",
                   "choices": ["forward", "reverse"] if "bidi_scan" in facilities else ["forward"],
                   "help": ("Forward preserves driver order; "
                            "reverse reverses positional association. "
                            "No target recognition is performed.")})
    if "has_scan_toll" in facilities:
        fields.append({"key": "scan_tolerance", "label": "Scan tolerance multiplier",
                       "type": "number", "required": False, "default": None,
                       "minimum": 0, "maximum": None, "exclusive_minimum": False, "unit": "",
                       "help": ("Leave unset normally to retain the driver default. Larger values "
                                "tolerate more within-patch variation and may accept "
                                "less-consistent scans.")})
    return {"layout_fields": fields, "layout_keys": sorted(f["key"] for f in fields),
            "required_layout_keys": sorted(f["key"] for f in fields if f["required"]),
            "max_patches": policy["max_patches"], "strip_requirement": policy["requirement"],
            "patch_association": "manual_returned_positions"}


def validate_layout(layout, workflow):
    """Use the exact descriptor rules; preserve omission instead of inserting defaults."""
    if not isinstance(layout, dict):
        raise ValueError("Layout must be an object")
    fields = {f["key"]: f for f in workflow["layout_fields"]}
    if layout.keys() - fields.keys():
        raise ValueError(f"Strip layout keys not consumed: {sorted(layout.keys() - fields.keys())}")
    missing = set(workflow["required_layout_keys"]) - layout.keys()
    if missing:
        raise ValueError(f"Strip layout requires {sorted(missing)}")
    for key, value in layout.items():
        _validate_value(fields[key], value)
