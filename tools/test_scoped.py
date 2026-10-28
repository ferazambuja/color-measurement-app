"""Run explicit component test profiles. This is not the full acceptance check.

Use tools/test_all.py for the full suite, lint, excerpts and installed package.
Profiles include shared contract tests; they are not automatic change-impact analysis.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from test_all import (
    authenticate_stub,
    discover_test_modules,
    mat_coverage,
    native_stub_coverage,
    renderer_coverage,
    require,
    require_renderer_dependencies,
    run_tests,
)

ROOT = Path(__file__).resolve().parents[1]
RECORDS = (
    "test_document_mat", "test_batch_archive", "test_dataset_namespace", "test_source_records",
    "test_workflow", "test_operation_records",
    "test_operation_exports", "test_spectral_exports", "test_ti3_exports",
    "test_chart_targets", "test_chart_state", "test_chart_sources", "test_cgats",
    "test_refresh_frequency", "test_mode_snapshot_refresh", "test_workflow_layout",
    "test_display_timing_frontend",
)
# The CGATS reader serves both retained chart targets and .ccmx/.ccss
# configuration; its checks belong in both records and transport profiles.
TRANSPORT = ("test_serial_transport", "test_serial_protocols", "test_serial_settings",
             "test_serial_instruments", "test_native_controls",
             "test_instrument_lifecycle",
             "test_argyll_native",
             "test_native_contribution", "test_import_contribution", "test_argyll_description",
             "test_argyll_capability", "test_native_mode_checks", "test_mode_acceptance",
             "test_instrument", "test_instrument_replies", "test_instrument_frontend",
             "test_display_types",
             "test_refresh_frequency",
             "test_discovery", "test_mode_options", "test_cgats",
             "test_colorimeter_correction", "test_strip_layout")

SOURCE_TESTS = (
    "test_calibration_repairs", "test_calibration_string_reader",
    "test_ss_xy_metadata", "test_ss_protocol",
    "test_display_timing", "test_display_timing_spectral",
    "test_spyder_calibration_slots", "test_spyder_black_cache",
    "test_munki_scan_margin", "test_spyder_selection_path", "test_munki_nonadaptive_calibration",
    "test_xyz_only_conversion", "test_conversion_allocation", "test_sanitizers",
    "test_disptype_list_release", "test_scan_finite", "test_munki_curve_release",
    "test_munki_white_buffer_release", "test_disptype_list_member_release",
    "test_munki_ledtemp_slope", "test_proof_case_discovery", "test_munki_dark_copy",
    "test_i1pro3_wavelength_finite",
    "test_scan_tolerance_option", "test_xcalstd_option", "test_ieee754_special",
    "test_ccss_cgats",
    "test_filter_values", "test_native_resync", "test_native_resync_package",
    "test_no_termination", "test_repair_provenance", "test_proof_inputs",
    "test_nonadaptive_calibration",
    "test_flash_capture", "test_scan_normalization", "test_legacy_native_build",
    "test_legacy_nonadaptive_calibration",
)
PROFILES = {
    "science": ("test_science", "test_science_s1", "test_science_rgb",
                "test_qualified_science", "test_desktop_science_materials", "test_reading_dataset",
                "test_xyz_comparison",
                "test_science_standard_white", "test_science_uvy_inverse", "test_presentation",
                "test_resource_checkout_bytes",
                "test_reflectance_fraction", "test_workflow"),
    "records": RECORDS,
    "application": ("test_application", "test_application_chart",
                    "test_desktop_session", "test_desktop_setup", "test_desktop_direct",
                    "test_desktop_science_materials", "test_activity",
                    "test_desktop_corrections",
                    "test_desktop_client",
                    "test_dataset_binding", "test_analysis_exports", "test_qualified_science",
                    "test_reading_dataset",
                    "test_xyz_comparison",
                    "test_desktop_host", "test_docs_application_example",
                    "test_presentation", "test_frontend", "test_demo",
                    "test_core_imports", *RECORDS),
    "frontend": ("test_application_chart", "test_frontend", "test_display_types",
                 "test_demo", "test_operator_setup",
                 "test_dataset_binding", "test_analysis_exports", "test_qualified_science",
                 "test_reading_dataset",
                 "test_xyz_comparison",
                 "test_application", "test_desktop_session", "test_desktop_setup",
                 "test_desktop_direct", "test_serial_instruments",
                 "test_desktop_science_materials", "test_activity",
                 "test_desktop_corrections",
                 "test_desktop_client",
                 "test_desktop_host",
                 "test_instrument_frontend", "test_mode_options",
                 "test_instrument_lifecycle", "test_event_frames", *RECORDS),
    # One acquisition profile, because there is one acquisition path. The two
    # per-device profiles it replaces named the modules that no longer exist.
    "instrument": (*TRANSPORT, *RECORDS, "test_reflectance_fraction"),
    "native": (*TRANSPORT, "test_native_serial_parity", *SOURCE_TESTS),
    "tooling": (
        "test_argyll_inventory", "test_doc_snippets", "test_documentation", "test_examples",
        "test_import_contribution", "test_repair_provenance", "test_resource_checkout_bytes",
        "test_docs_application_example",
        "test_test_all", "test_test_scoped", "test_core_imports",
        "test_bench_session",
    ),
}


def select_modules(profiles, *, root=ROOT):
    """Require an explicit nonempty union and account for every test module."""
    require(bool(profiles), "Select at least one profile, or use --list")
    unknown = set(profiles) - PROFILES.keys()
    require(not unknown, f"Unknown profiles: {', '.join(sorted(unknown))}")
    registered = {f"tests/{module}.py" for modules in PROFILES.values() for module in modules}
    discovered = set(discover_test_modules(root))
    require(registered == discovered,
            "Test-profile inventory is stale; register new modules or remove missing paths. "
            f"Unregistered: {sorted(discovered - registered)}; "
            f"missing: {sorted(registered - discovered)}")
    modules = tuple(dict.fromkeys(module for name in profiles for module in PROFILES[name]))
    require(bool(modules), "Selected profiles contain no test modules")
    return modules


def check_report(report, modules):
    """Do not turn an empty/missing/deselected module into a passing profile."""
    cases = list(ET.parse(report).iter("testcase"))
    require(bool(cases), "Focused pytest report contains no test cases")
    observed = set()
    skipped = []
    for case in cases:
        parts = case.get("classname", "").split(".")
        matches = [module for module in modules if module in parts]
        require(len(matches) == 1, "Focused pytest reported an unselected or unidentified module")
        observed.add(matches[0])
        require(case.find("failure") is None and case.find("error") is None,
                "Focused pytest report contains a failed case")
        skip = case.find("skipped")
        if skip is not None:
            skipped.append((matches[0], skip.get("message", "unspecified skip")))
    require(observed == set(modules),
            f"Selected test modules were not collected: {sorted(set(modules) - observed)}")
    return len(cases), skipped


def main(argv=None):
    from color_measurement.native_receipt import native_worker_name

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profiles", nargs="*", help="One or more explicit profile names")
    parser.add_argument("--list", action="store_true", help="List profiles and their exact modules")
    parser.add_argument("--require-mat", action="store_true",
                        help="Require MAT tests; selected profiles must include records/exports")
    parser.add_argument("--generic-stub", type=Path,
                        help="Existing trusted generic-session-stub-worker")
    parser.add_argument("--chart-stub", type=Path,
                        help="Existing trusted generic-chart-stub-worker")
    parser.add_argument("--argyll-archive", type=Path,
                        help="Pinned ZIP for native profile; no download or real helper")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1,
                        help="Test modules run at once (default: CPU count)")
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    try:
        names = args.profiles or (tuple(PROFILES) if args.list else ())
        modules = select_modules(names)
        if args.list:
            require(not (args.require_mat or args.generic_stub or args.chart_stub
                         or args.argyll_archive), "--list cannot include execution options")
            for name in dict.fromkeys(names):
                print(f"{name}: {' '.join('tests/' + item + '.py' for item in PROFILES[name])}")
            print("Explicit component checks only; full acceptance: python tools/test_all.py")
            return 0

        if args.require_mat:
            require("test_operation_exports" in modules,
                    "--require-mat needs test_operation_exports (e.g. the records profile)")
        if "test_desktop_client" in modules:
            require_renderer_dependencies()
        # Honor relevant existing stub variables, but never select another
        # family's integration implicitly through an unrelated profile.
        environment = {}
        stubs = {}
        # The substituted workers the one acquisition path uses. Both drive the
        # SAME ladder; they differ only in which instruments they substitute.
        for family, worker in (("generic", "generic-session-stub-worker"),
                               ("chart", "generic-chart-stub-worker")):
            key = f"COLOR_MEASUREMENT_{family.upper()}_STUB"
            explicit = getattr(args, f"{family}_stub")
            relevant = bool({"test_instrument", "test_instrument_frontend"} & set(modules))
            require(not explicit or relevant,
                    f"--{family}-stub needs a profile containing test_instrument")
            supplied = explicit or (os.environ.get(key) if relevant else None)
            path = Path(supplied).resolve() if supplied else None
            if path is not None:
                require(path.is_file() and path.name == native_worker_name(worker),
                        f"Use an existing dedicated {worker}")
                authenticate_stub(path)
            stubs[family] = path
            environment[key] = str(path) if path else ""
        if args.argyll_archive:
            require(any(module in SOURCE_TESTS for module in modules),
                    "--argyll-archive needs the native profile")
            require(args.argyll_archive.is_file(), "Explicit Argyll archive is not a file")
            environment["CM_ARGYLL_ARCHIVE"] = str(args.argyll_archive.resolve())
        print(f"Focused pytest profiles: {', '.join(dict.fromkeys(names))}", flush=True)
        print("Selected modules: " + ", ".join(modules), flush=True)
        print("Full acceptance is not being run.", flush=True)
        with tempfile.TemporaryDirectory(prefix="color-measurement-focused-") as directory:
            report = Path(directory) / "pytest.xml"
            run_tests(report, environment, args.jobs, [f"tests/{module}.py" for module in modules])
            count, skipped = check_report(report, modules)
            if "test_desktop_client" in modules:
                print(renderer_coverage(report))
            # Both substituted workers report against `test_instrument`, because
            # both drive the same acquisition path. Keyed on the module that holds
            # the anchors rather than on a per-family module name that no longer
            # exists -- while it did, this condition silently never matched and a
            # skipped anchor stopped being an error.
            if "test_instrument" in modules:
                for family, path in stubs.items():
                    print(native_stub_coverage(report, requested=path is not None,
                                               family=family))
            if "test_operation_exports" in modules:
                print(mat_coverage(report, requested=args.require_mat))
            for module, reason in sorted(set(skipped)):
                print(f"SKIPPED {module}: {reason}")
            print(f"PASS: focused pytest ({count} reported cases, {len(skipped)} skipped). "
                  "Full acceptance remains a separate command.", flush=True)
        return 0
    except KeyboardInterrupt:
        print("CANCELLED: focused tests incomplete.", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError, ET.ParseError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: focused tests incomplete: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
