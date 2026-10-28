"""Validate and describe exact-word checks from an initialized native lifetime.

These checks precede measurement mode/options selection. Acquisition's existing
retained-calibration request may already have run; these are not calibration
facts. They neither certify a later configuration nor replace acquisition's
check_mode/set_mode calls.
"""

from __future__ import annotations

from .argyll_capability import baseline_mode_words, mode_word

BASELINE_SCOPE = "initialized_before_mode_configuration"


def _uint(value) -> bool:
    return type(value) is int and 0 <= value <= 0xffffffff


def validate_baseline_mode_checks(value, *, mode_capabilities: int | None = None) -> dict:
    """Return a detached, validated native fact; missing facts are not wire-valid.

    The list covers exactly the bounded candidates advertised by its initial
    mask. A later mask may advertise a word absent from this baseline.
    ``mode_capabilities`` is supplied only when the caller has the same initial
    mask, not an acquisition connection's later, mode-dependent capabilities.
    """
    if (not isinstance(value, dict)
            or set(value) != {"scope", "mode_capabilities", "checks"}
            or value["scope"] != BASELINE_SCOPE
            or not _uint(value["mode_capabilities"])
            or not isinstance(value["checks"], list)):
        raise ValueError("Invalid baseline mode checks")
    primary = value["mode_capabilities"]
    if mode_capabilities is not None and primary != mode_capabilities:
        raise ValueError("Baseline mode checks refer to a different initial capability mask")
    candidates = set(baseline_mode_words(primary))
    seen = set()
    checks = []
    for check in value["checks"]:
        if (not isinstance(check, dict) or set(check) != {"mode_word", "native_code"}
                or not _uint(check["mode_word"])
                or check["mode_word"] not in candidates or check["mode_word"] in seen
                or (check["native_code"] is not None and not _uint(check["native_code"]))):
            raise ValueError("Invalid, duplicate or unadvertised baseline mode check")
        seen.add(check["mode_word"])
        checks.append(dict(check))
    if seen != candidates:
        raise ValueError("Baseline mode checks omit an advertised candidate")
    return {"scope": BASELINE_SCOPE, "mode_capabilities": primary, "checks": checks}


def _acceptance(facts: dict | None, word: int) -> dict:
    status, code, reason = "unknown", None, "baseline_not_observed"
    if facts is not None:
        reason = "mode_not_checked"
        for check in facts["checks"]:
            if check["mode_word"] != word:
                continue
            code = check["native_code"]
            if code is None:
                reason = "check_mode_unavailable"
            elif code == 0:
                status, reason = "confirmed", None
            elif code & 0xff0000 == 0x50000:  # inst.h: inst_mask / inst_unsupported
                status, reason = "rejected", "native_mode_rejected"
            else:
                reason = "native_check_inconclusive"
            break
    return {"mode_word": word, "status": status, "native_code": code, "reason": reason}


def baseline_native_mode(facts: dict | None, mode: str, *, spectral: bool) -> dict:
    """Describe the baseline word for each offered output selection.

    ``default`` means the descriptor's unmodified spectral/XYZ selection, never
    preferred geometry. Filter, resolution and display type are not certified.
    Facts must have passed ``validate_baseline_mode_checks`` or be absent.
    """
    return {
        "scope": BASELINE_SCOPE,
        "default": _acceptance(facts, mode_word(mode, spectral=spectral)),
        "xyz_only": _acceptance(facts, mode_word(mode, spectral=False)) if spectral else None,
    }
