"""Sanitizer runs of the source-bound proofs fail closed.

Proof runners using this helper compile sanitized probes with AddressSanitizer
and UndefinedBehaviorSanitizer.
With the compilers' default
recovery an undefined-behaviour diagnostic is printed and the process goes on
to exit 0 with its JSON intact, so a runner that checks only the exit status
and parses the JSON reports "passed" over the diagnostic. ``flags`` disables
that recovery and ``refuse`` rejects a diagnostic in a run's stderr before any
result is read, whatever the exit status. This helper keeps those flags and
diagnostic checks together for its callers.
"""

from __future__ import annotations

# GCC's undefined group omits float-to-integer overflow; require the same cast
# instrumentation explicitly on every compiler used for these source proofs.
FLAGS = ("-fsanitize=address,undefined,float-cast-overflow",
         "-fno-sanitize-recover=all", "-fno-omit-frame-pointer")
# Numerical before/after controls deliberately exercise pre-repair source.
# Keep their UBSan-only scope, with the same explicit cast check as FLAGS.
UNDEFINED_FLAGS = ("-fsanitize=undefined,float-cast-overflow", "-fno-sanitize-recover=all")
#: clang's `undefined` group excludes float division by zero; a proof that must see a
#: real division by zero names this set, from here, on the one build that needs it.
FLOAT_DIVIDE_BY_ZERO = ("-fsanitize=float-divide-by-zero",
                        "-fno-sanitize-recover=float-divide-by-zero")
MARKERS = ("runtime error:", "ERROR: AddressSanitizer", "ERROR: LeakSanitizer",
           "UndefinedBehaviorSanitizer:")


def flags(sanitize: bool) -> list[str]:
    """The compiler flags a sanitized run adds; none otherwise."""
    return list(FLAGS) if sanitize else []


def diagnostic(stderr: str) -> str | None:
    """The first sanitizer diagnostic line in a run's stderr, or None."""
    for line in stderr.splitlines():
        if any(marker in line for marker in MARKERS):
            return line
    return None


def refuse(stderr: str, label: str) -> None:
    """Fail the proof on a sanitizer diagnostic before its result is read."""
    if diagnostic(stderr) is not None:
        raise AssertionError(f"Sanitizer diagnostic in {label}; see log")
