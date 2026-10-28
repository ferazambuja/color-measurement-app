"""Source-backed operator placement and possible calibration setup.

Each entry is one short instruction a client can show on its prompt card, with
the document, page and a verbatim quote (25 words or fewer) that back it. Keys
are the Argyll instrument name the frontend binds (``DeviceIdentity.model``) and
the upstream route (``parse_mode_name(mode)[0]``); ``calibration`` is the
instrument's own calibration setup. A route a manual does not describe has no
entry and publishes null: nothing here is inferred from another model or mode.
Per-mode calibration previews additionally use the pinned driver's condition
selection. They describe possible references, never current calibration readiness.

The documents live outside this repository, with provenance, in the private
instrument reference (``xrite/primary-downloads-2026-09-25.json``). The ColorMunki
Photo/Design guide could not be obtained, so its entries cite X-Rite's support
FAQs by URL, and they say only which dial position to use.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .argyll_capability import CAPABILITY2, condition_detail, parse_mode_name

__all__ = ["calibration_setup", "calibration_setups", "mode_calibration_setups", "mode_setup",
           "physical_chart_setup"]


@dataclass(frozen=True)
class _Setup:
    text: str
    document: str
    location: str      # a PDF page, or the URL of a web page without pages
    quote: str
    official: bool = True

    def published(self) -> dict:
        return asdict(self)


_I1D3 = "X-Rite i1Display Pro Quick Start Guide, EODIS3-550 (12/10)"
_I1PRO2 = "X-Rite i1Pro 2 User Manual (eo2-qsg)"
_I1PRO3 = "X-Rite i1Pro 3 User Manual"
_MUNKI_MODES = ("https://www.xrite.com/service-support/"
                "measurement_modes_of_the_colormunki_photo_or_colormunki_design")
_MUNKI_CAL = ("https://www.xrite.com/service-support/"
              "calibrating_the_colormunki_photo_or_colormunki_design")
_PULSE = "X-Rite PULSE Spectrophotometer User Guide (DTP20), 2005, archived X-Rite copy"
_DTP41 = "X-Rite AutoScan Spectrophotometer Operator's Manual, DTP41-500 Rev. O"
_DTP41_SPOT = "X-Rite Tech Note: Spot Reading Using the DTP41"
_DTP51 = "X-Rite DTP 50 Series AutoScan Colorimeter Operation Manual (DTP51 & DTP52)"
_SPECTROLINO = "GretagMacbeth Spectrolino / SpectroScan / SpectroScan T Operating Manual"
_DTP22_CAL = "https://www.xrite.com/service-support/using_hyperterminal_to_calibrate_a_dtp22"
_KLEIN_FAQ = "https://www.kleininstruments.com/faq"

_MUNKI_D = _Setup(
    "Turn the dial to position D (monitor, chart scanning and spot).", "X-Rite support FAQ",
    _MUNKI_MODES, "D = Monitor, Chart Scanning, and Monitor Measurement Mode")
_PULSE_STRIP = _Setup(
    "Seat the PULSE on the Pathfinder guide over a target backed so white shows on "
    "both sides; start and end on white.", _PULSE, "PDF p. 17",
    "Position the instrument on the Pathfinder guide. Make sure the instrument is seated "
    "properly on the guide.", official=False)
_SPECTROSCAN_TABLE = _Setup(
    "Before power-on, mount the Spectrolino's white reference in the table's upper "
    "slot; lay the chart (1.5 mm at most) on the electrostatic surface.", _SPECTROLINO,
    "PDF p. 27", "Point 1: Mount the white reference (17) from the Spectrolino into the "
    "upper slot (38) of the SpectroScan.")

_SETUP: dict[str, dict[str, _Setup]] = {
    "Klein K-10": {
        "emis_ambient": _Setup(
            "Fit the diffuser and select the instrument's named Lux calibration for "
            "ambient readings; aim the diffuser at the light source.",
            "Klein Instruments FAQ", _KLEIN_FAQ,
            "In this case, you would also use the Lux calibration file #2."),
    },
    "X-Rite i1 DisplayPro, ColorMunki Display": {
        "emis_spot": _Setup(
            "Swing the diffuser arm to the rear, then hang the unit on the centre of the "
            "screen with its foam pad flat and the counterweight behind.", _I1D3, "PDF p. 21",
            "To mount the device on your display, position the optics in contact with the "
            "center of your display."),
        "emis_tele": _Setup(
            "Set the diffuser arm halfway back as a stand, or use a tripod, and aim the "
            "optics at the centre of the projector screen.", _I1D3, "PDF p. 31",
            "Rotate the device up or down and pivot left or right until the device optics "
            "are pointing towards the center on the projector screen."),
        "emis_ambient": _Setup(
            "Snap the diffuser over the optics and set the unit beside the display, "
            "diffuser facing up.", _I1D3, "PDF p. 25",
            "Press down on the ambient diffuser arm until it snaps into place over the optics."),
    },
    "X-Rite i1 Pro 2": {
        "ref_spot": _Setup(
            "Mount the i1Pro on the positioning target and place it on the patch.", _I1PRO2,
            "PDF p. 25", "To prepare your i1Pro device for spot measurement you need to "
            "mount the i1Pro device on the positioning target."),
        "ref_strip": _Setup(
            "Clamp the chart on the backup board, seat the i1Pro in the ruler, and start "
            "on the white paper before the selected row.", _I1PRO2, "PDF p. 20",
            "Position the ruler with the device on the white paper in front of row #1 of "
            "your test chart."),
        "emis_spot": _Setup(
            "Seat the i1Pro's nose in the display holder and hang it in the middle of the "
            "screen.", _I1PRO2, "PDF p. 27",
            "Make sure that the nose of the i1Pro device seats firmly in the display holder."),
        "emis_tele": _Setup(
            "Mount the i1Pro in the tripod holder and aim its aperture at the centre of the "
            "screen.", _I1PRO2, "PDF p. 33",
            "The limiting aperture of the i1Pro should point towards the center of your screen."),
        "emis_ambient": _Setup(
            "Fit the ambient head with the matching serial number on the aperture, lock it, "
            "and pull off its cover.", _I1PRO2, "PDF p. 29",
            "Before you start measuring, please attach the ambient light measuring head on "
            "the limiting aperture of the i1Pro device."),
    },
    "X-Rite i1 Pro 3": {
        "ref_spot": _Setup(
            "Mount the i1Pro 3 on the positioning target, place it on the patch and press "
            "it down.", _I1PRO3, "PDF p. 12",
            "Position the device on the patch you would like to measure and press the "
            "device down to the positioning target."),
        "ref_strip": _Setup(
            "Seat the i1Pro 3 in the ruler on the backer board and start on the substrate "
            "before the selected row.", _I1PRO3, "PDF p. 11",
            "Position the ruler with the device on the substrate in front of row #1 of your "
            "test chart."),
        "emis_spot": _Setup(
            "Mount the i1Pro 3 in the display holder with its aperture in the middle of the "
            "screen.", _I1PRO3, "PDF p. 13",
            "Fix the limiting aperture of the i1Pro 3 measurement device in the middle of "
            "your screen."),
        "emis_tele": _Setup(
            "Mount the i1Pro 3 in the tripod holder and aim its aperture at the centre of "
            "the screen.", _I1PRO3, "PDF p. 16",
            "The limiting aperture of the i1Pro 3 should point towards the center of your "
            "screen."),
        "emis_ambient": _Setup(
            "Fit the ambient head with the matching serial number on the aperture and "
            "turn it clockwise until it stops.", _I1PRO3, "PDF p. 14",
            "Rotate the ambient light measuring head clockwise until it stops."),
    },
    "X-Rite ColorMunki": {
        "emis_ambient": _Setup(
            "Turn the dial to position A (ambient).", "X-Rite support FAQ", _MUNKI_MODES,
            "A = Ambient Light Measurement Mode"),
        "emis_tele": _Setup(
            "Turn the dial to position B (projector).", "X-Rite support FAQ", _MUNKI_MODES,
            "B = Projector Measurement Mode"),
        "emis_spot": _MUNKI_D,
        "ref_spot": _MUNKI_D,
        "ref_strip": _MUNKI_D,
    },
    "X-Rite DTP20": {
        "ref_strip": _PULSE_STRIP,
        "ref_chart": _PULSE_STRIP,
        "ref_spot": _Setup(
            "Seat the PULSE in the spot reader guide centred on a patch of at least 6.5 mm, "
            "and press the instrument down.", _PULSE, "PDF p. 24",
            "Position the instrument in the spot reader guide.", official=False),
    },
    "X-Rite DTP41": {
        "ref_strip": _Setup(
            "Leave a 1.5 in leader, put the patch column under the alignment mark and "
            "insert the strip to the rear rollers.", _DTP41, "PDF p. 11",
            "Position the strip so that the group of patches to measure is directly under "
            "alignment mark on the cover."),
        "ref_spot": _Setup(
            "Sandwich the sample between the template's sheets and insert them.",
            _DTP41_SPOT, "PDF p. 1", "Insert the template and sample into the instrument."),
    },
    "X-Rite DTP51": {
        "ref_strip": _Setup(
            "Set the strip guide to the stop number on the display and feed the strip after "
            "a 1.2 in leader.", _DTP51, "PDF p. 19",
            "the strip guide pointer must be positioned to the corresponding stop location "
            "number appearing in the instrument display."),
    },
    "GretagMacbeth SpectroScan": {
        "ref_spot": _SPECTROSCAN_TABLE,
        "ref_xy": _SPECTROSCAN_TABLE,
        "emis_spot": _Setup(
            "Fit the NO filter; emissive calibration needs the Spectrolino light-tight on "
            "its white-reference plate.", _SPECTROLINO, "PDF p. 17",
            "Emissive measurements may only be performed using the NO filter."),
    },
}


#: The manuals' calibration setup, keyed by the calibration CONDITION it describes
#: (``calibration_condition`` names), because an instrument asks for more than one:
#: the ColorMunki's dial goes to its calibration position for ``man_cal_smode``
#: but must be back at surface for a transmission white (munki_imp.c:1071-1073).
#: A prompt shows only the entry for its own condition; a condition no manual
#: describes has none. ``None`` is a calibration the instrument does with no
#: operator condition at all, which no prompt carries.
_CALIBRATION: dict[str, dict[str | None, _Setup]] = {
    "X-Rite DTP22": {   # dtp22.c:714-738: the physical read switch triggers white
        "man_ref_whitek": _Setup(
            "Press the DTP22 down into its calibration holder when prompted; its "
            "physical read switch starts the white-reference reading.",
            "X-Rite support FAQ", _DTP22_CAL,
            'Immediately after sending the "ca" (calibrate) command, press the DTP22 '
            "down into its calibration holder."),
    },
    "X-Rite i1 Pro 2": {   # i1pro_imp.c:1742,1808,2224
        "man_ref_white": _Setup(
            "Open the calibration plate's slider and seat the i1Pro firmly on its white "
            "tile; the plate's serial number must match the instrument's.", _I1PRO2,
            "PDF p. 15", "Place the i1Pro measurement device on the calibration plate. "
            "The device must seat firmly on the calibration plate."),
    },
    "X-Rite i1 Pro 3": {   # i1pro3_imp.c:1492-1502
        "man_ref_white": _Setup(
            "Open the calibration plate's slider and seat the i1Pro 3 firmly on its white "
            "tile; the plate's serial number must match the instrument's.", _I1PRO3,
            "PDF p. 8", "Place the i1Pro 3 measurement device on the calibration plate. "
            "The device must seat firmly on the calibration plate."),
    },
    "X-Rite ColorMunki": {   # munki_imp.c:1061-1069
        "man_cal_smode": _Setup(
            "Turn the dial to its calibration position and leave it there for calibration.",
            "X-Rite support FAQ", _MUNKI_CAL,
            "Turn the Rotary Disk to the calibration position."),
    },
    "X-Rite DTP20": {   # dtp20.c:1183
        "man_ref_white": _Setup(
            "Lift the cover off the reference in the Pathfinder guide (serial numbers must "
            "match) and set the PULSE over it.", _PULSE, "PDF p. 14",
            "Position the instrument over the calibration reference on the Pathfinder guide.",
            official=False),
    },
    "X-Rite DTP41": {   # dtp41.c:976; its transmission white, :964, has no note
        "uop_ref_white": _Setup(
            "Insert the instrument's own Color Reflection Reference under the alignment "
            "mark and hold the button for three seconds.", _DTP41, "PDF p. 10",
            "Center the designated end of the Color Reflection Reference under the "
            "alignment mark."),
    },
    "X-Rite DTP51": {   # dtp51.c:643
        "uop_ref_white": _Setup(
            "Press cal, then insert the Auto-Cal strip into the 35 mm slot until it stops.",
            _DTP51, "PDF p. 25",
            "Insert the Auto-Cal strip into the 35mm slot until it stops against the drive "
            "rollers."),
    },
    "GretagMacbeth SpectroScan": {   # no operator condition: ss.c:1452-1462
        None: _Setup(
            "Nothing to place: the SpectroScan calibrates the Spectrolino on its white "
            "reference by itself.", _SPECTROLINO, "PDF p. 29",
            "After having inserted the last position, an absolute white calibration of the "
            "Spectrolino is performed automatically"),
    },
}


def mode_setup(instrument: str, route: str) -> dict | None:
    """The manual's setup for this instrument's route, or None where none is stated."""
    entry = _SETUP.get(instrument, {}).get(route)
    return None if entry is None else entry.published()


# This is an assessment of the documented optical setup, not a capability bit
# or hardware acceptance. In particular, a tele route and a returned spectrum
# do not establish a measurement field suitable for isolating a paper patch.
# Naming-only admission still requires the operator to confirm the actual
# illuminated patch, aperture/footprint, distance and placement. It never makes
# radiance or luminance into reflectance or establishes reference comparability.
_NAMING_ONLY = (
    "Patch naming from radiance or luminance only. Confirm that the illuminated "
    "patch fills the actual measurement field at the chosen distance. The app "
    "does not verify fitted optics, placement or illumination.")
_DISPLAY_ONLY = (
    "The documented setup measures a display or projector screen. Physical-chart "
    "patch optics have not been assessed; this does not claim that measuring "
    "another surface is physically impossible.")
_PHYSICAL_CHART = {
    "Colorimetry Research CR-100": ("naming_only", _Setup(
        _NAMING_ONLY, "Colorimetry Research CR-100 specifications",
        "https://www.colorimetryresearch.com/products/cr-100",
        "Spot size (mm) = 23.0 mm + 0.08732 * distance to object (mm)")),
    "Colorimetry Research CR-250": ("naming_only", _Setup(
        _NAMING_ONLY, "Colorimetry Research CR-250 specifications",
        "https://www.colorimetryresearch.com/products/cr-250",
        "Direct viewing and direct measuring system")),
    "Klein K-10": ("naming_only", _Setup(
        _NAMING_ONLY, "Klein Instruments FAQ: measured area",
        _KLEIN_FAQ, "The aiming lights put up a circle of 8 red dots around the measured area.")),
    "X-Rite i1 DisplayPro, ColorMunki Display": ("display_only", _Setup(
        _DISPLAY_ONLY, _I1D3, "PDF p. 21",
        "To mount the device on your display, position the optics in contact with the "
        "center of your display.")),
    "Datacolor SpyderX": ("display_only", _Setup(
        _DISPLAY_ONLY, "Datacolor SpyderX User Guide",
        "https://www.datacolor.com/spyder/wp-content/uploads/2023/04/"
        "SpyderX-Users-Guide-FINAL-EN.pdf",
        "Here you will choose whether you are using a Desktop Display, Laptop Display, "
        "or Projector.")),
    "Datacolor SpyderX2": ("display_only", _Setup(
        _DISPLAY_ONLY, "Datacolor: Welcome to Spyder X2",
        "https://www.datacolor.com/spyder/welcome-to-spyder-x2/",
        "Remove the lens cap from your Spyder X2 and hang the unit over your display, "
        "when prompted.")),
    "Datacolor Spyder 2024": ("display_only", _Setup(
        _DISPLAY_ONLY, "Datacolor: Welcome to Spyder (2024)",
        "https://www.datacolor.com/spyder/welcome-to-spyder/",
        "The device should sit flush on the monitor under its own weight, do not hold "
        "it against the screen with your hand.")),
}
_REFLECTION_CHART = {
    "ref_spot": "Reflection spot measurement mode",
    "ref_strip": "Reflection strip measurement mode",
    "ref_xy": "Reflection X-Y measurement mode",
    "ref_chart": "Reflection Chart measurement mode",
}


def physical_chart_setup(instrument: str, route: str) -> dict:
    """Source-backed physical-target scope; unknown where optics are unassessed.

    The caller publishes this only for modes the bound instrument advertises.
    Clients consume ``kind`` rather than interpreting mode names or this prose.
    ``assessment`` uses the same source-row shape as operator setup.
    """
    if instrument in _DRIVER_FOR_INSTRUMENT:
        if route in _REFLECTION_CHART:
            return {"kind": "reflective", "assessment": _Setup(
                "The native route measures reflection. Patch isolation and operator "
                "placement still follow the instrument's setup.",
                "ArgyllCMS 3.5.0, spectro/inst.h", _SOURCE_ARCHIVE,
                _REFLECTION_CHART[route]).published()}
        if route in {"emis_spot", "emis_tele"} and instrument in _PHYSICAL_CHART:
            kind, entry = _PHYSICAL_CHART[instrument]
            assessment = entry.published()
            manual = _SETUP.get(instrument, {}).get(route)
            if kind == "display_only" and manual is not None:
                assessment = {**manual.published(), "text": _DISPLAY_ONLY}
            return {"kind": kind, "assessment": assessment}
    return {"kind": "unknown", "assessment": None}


def calibration_setup(instrument: str, condition: str | None) -> dict | None:
    """The manual's setup for this calibration condition, or None where none is stated."""
    entry = _CALIBRATION.get(instrument, {}).get(condition)
    return None if entry is None else entry.published()


def calibration_setups(instrument: str) -> list[dict]:
    """Every calibration setup the manuals state for this instrument, with its condition."""
    return [{"condition": condition, **entry.published()}
            for condition, entry in _CALIBRATION.get(instrument, {}).items()]


# These are the actual driver owners, not acquisition or calibration implementations.
# get_n_a_cals/calibrate in each pinned source select the conditions below. Keep
# subtype exceptions here; a client must not infer references from a family name.
_CALIBRATION_DRIVERS = {
    "inst.c": ("Hughski ColorHug", "Hughski ColorHug2", "Colorimtre HCFR",
               "GretagMacbeth Huey"),
    "cr250.c": ("Colorimetry Research CR-100", "Colorimetry Research CR-250",
                "Colorimetry Research CR-300"),
    "pr655.c": ("Photo Research PR-655", "Photo Research PR-670"),
    "i1pro_imp.c": ("GretagMacbeth i1 Monitor", "GretagMacbeth i1 Pro", "X-Rite i1 Pro 2"),
    "i1pro3_imp.c": ("X-Rite i1 Pro 3",),
    "munki_imp.c": ("X-Rite ColorMunki",),
    "i1disp.c": ("GretagMacbeth i1 Display 1", "GretagMacbeth i1 Display 2",
                 "ColorMunki Smile"),
    "i1d3.c": ("X-Rite i1 DisplayPro, ColorMunki Display",),
    "spyd2.c": ("ColorVision Spyder1", "ColorVision Spyder2", "Datacolor Spyder3",
                "Datacolor Spyder4", "Datacolor Spyder5"),
    "spydX.c": ("Datacolor SpyderX",),
    "spydX2.c": ("Datacolor SpyderX2", "Datacolor Spyder 2024"),
    "dtp20.c": ("X-Rite DTP20",),
    "dtp22.c": ("X-Rite DTP22",),
    "dtp41.c": ("X-Rite DTP41",),
    "dtp51.c": ("X-Rite DTP51",),
    "dtp92.c": ("X-Rite DTP92", "X-Rite DTP94"),
    "ex1.c": ("Image Engineering EX1",),
    "kleink10.c": ("Klein K-10",),
    "smcube.c": ("SwatchMate Cube",),
    "ss.c": ("GretagMacbeth Spectrolino", "GretagMacbeth SpectroScan",
             "GretagMacbeth SpectroScanT"),
    "specbos.c": ("JETI specbos", "JETI specbos 1201", "JETI specbos 2501",
                  "JETI spectraval"),
}
_DRIVER_FOR_INSTRUMENT = {name: driver for driver, names in _CALIBRATION_DRIVERS.items()
                          for name in names}
_SOURCE_ARCHIVE = "https://www.argyllcms.com/Argyll_V3.5.0_src.zip"
#: Drivers from the bundled contribution, attributed separately from Argyll.
_CONTRIBUTED_DRIVERS = {"cr250.c": "CR", "pr655.c": "PR"}


def _preview(instrument: str, driver: str, condition: str | None, *,
             text: str | None = None, quote: str | None = None) -> dict:
    manual = _CALIBRATION.get(instrument, {}).get(condition)
    if manual is not None and quote is None:
        entry = manual.published()
        if text is not None:
            entry["text"] = text
    else:
        document = f"ArgyllCMS 3.5.0, spectro/{driver}"
        location = _SOURCE_ARCHIVE
        if driver in _CONTRIBUTED_DRIVERS:
            # This contribution is composed onto Argyll, not part of its archive.
            document = (f"Color Measurement {_CONTRIBUTED_DRIVERS[driver]} contribution, "
                        f"spectro/{driver}")
            location = ("Bundled source: tools/native_argyll/contributions/cr_pr/"
                        f"files/spectro/{driver}")
        entry = _Setup(
            text or condition_detail(condition)["instruction"],
            document, location,
            quote or f"*calc = inst_calc_{condition};",
        ).published()
    return {"condition": condition, **entry}


def mode_calibration_setups(instrument: str, mode: str, secondary: int) -> list[dict]:
    """Possible setup for this mode, independent of today's calibration state.

    Native validity, age, selected display calibration and hardware variant still
    decide what is actually needed. The operation's typed prompt is authoritative.
    Optional-only calibrations are explained without instructing their execution:
    the application requests needed calibrations, not every available calibration.
    Stored reads have no acquisition setup. An unassessed driver returns no notes.
    """
    route, flags, _ = parse_mode_name(mode)
    driver = _DRIVER_FOR_INSTRUMENT.get(instrument)
    if route.startswith("s_ref_") or driver is None:
        return []
    reflection = route.startswith("ref_")
    transmission = route.startswith("trans_")
    emission = route.startswith("emis_")
    ambient = route in {"emis_ambient", "emis_ambient_flash"}
    notes = []

    def add(condition: str | None, text: str | None = None, quote: str | None = None):
        notes.append(_preview(instrument, driver, condition, text=text, quote=quote))

    if driver in {"i1pro_imp.c", "i1pro3_imp.c"}:
        monitor = instrument == "GretagMacbeth i1 Monitor"
        if reflection or driver == "i1pro3_imp.c" or (
                instrument == "X-Rite i1 Pro 2" and not ambient):
            add("man_ref_white")
        elif not monitor:
            add("man_ref_white", "High-resolution fine calibration can need the reflective "
                "white reference. Place it only if the white-reference prompt asks.")
        if instrument == "X-Rite i1 Pro 2" and ambient:
            add("man_am_dark", "Wavelength calibration can need the ambient adapter with "
                "its cap fitted. Follow that prompt before returning to ambient sampling.")
        if emission:
            add("man_em_dark")
            if "emis_nonadaptive" in flags and route != "emis_strip":
                add("emis_white", "Nonadaptive exposure selection can need a 100% white "
                    "display patch; return to the sample after the prompted calibration.")
        if transmission:
            add("man_trans_dark")
            add("man_trans_white")
    elif driver == "munki_imp.c":
        add("man_cal_smode")
        if transmission:
            add("man_trans_white", "For transmission white, return the dial to surface "
                "position and present the unobstructed transmissive white source.")
        if emission and "emis_nonadaptive" in flags and route != "emis_strip":
            add("emis_white")
    elif driver in {"i1disp.c", "i1d3.c", "spyd2.c", "specbos.c"}:
        # These drivers retain refresh mode in ambient/transmission too. The
        # observed facility excludes ColorMunki Display and a corrected Smile;
        # a label alone cannot distinguish the shared i1Display3 constructor.
        if secondary & CAPABILITY2["emis_refr_meas"]:
            add("emis_80pc", "If the selected display calibration uses refresh and no "
                "rate is set, refresh calibration can ask for an 80% white display patch.")
        if driver == "i1disp.c" and instrument == "GretagMacbeth i1 Display 1":
            add(None, "Its black calibration is optional, not requested by ordinary "
                "Measure or Calibrate. No black-reference preparation is required here.",
                "a_cals |= inst_calt_emis_offset;")
        if driver == "specbos.c" and transmission:
            add("man_trans_white")
    elif driver == "spydX.c":
        if not ambient:
            add("man_em_dark", "Black-offset calibration can ask you to cover the "
                "optical input before returning to the display sample.")
    elif driver == "spydX2.c":
        if not ambient:
            add(None, "Black calibration is optional in this build. Measure and "
                "Calibrate do not request a black reference for this mode.",
                "#undef ENABLE_BLACK_CAL")
    elif driver in {"dtp20.c", "dtp22.c", "dtp41.c", "dtp51.c"}:
        if driver == "dtp20.c":
            add("man_ref_white")
        elif driver == "dtp22.c":
            add("man_ref_whitek")
        elif driver == "dtp41.c":
            add("uop_trans_white" if transmission else "uop_ref_white")
        else:
            add("uop_ref_white")
    elif driver == "dtp92.c":
        add("man_em_dark")
        if instrument == "X-Rite DTP92":
            add("emis_grey", "Cell-ratio calibration can need a grey display patch. "
                "Follow any later lighter or darker patch prompt.")
    elif driver == "ex1.c":
        add("man_em_dark", "Dark calibration can require blocking the optical input; "
            "remove the block and return to the source only after calibration.")
    elif driver == "smcube.c":
        add("man_ref_white")
        add(None, "Light-trap and gloss-black calibration are optional; ordinary Measure "
            "or Calibrate requests only the needed white-reference calibration.",
            "a_cals |= inst_calt_ref_dark;")
    elif driver == "ss.c":
        if instrument == "GretagMacbeth Spectrolino":
            add("man_ref_white", "Calibration can need the matched reflective white "
                "reference, including before emissive or transmission readings.")
            if transmission:
                add("man_trans_white")
        else:
            text = "The table moves to its mounted white reference automatically when " \
                   "calibration is needed; keep that reference installed."
            quote = "/* For SpectroScan, move to the white reference in slot 1 and lower */"
            if transmission:
                text += " Fit the desired transmission aperture before calibration."
                quote = "It is assumed that the desired transmission aperture is fitted"
            add(None, text, quote)
        add("change_filter", "A filter mismatch can ask for the filter named by the "
            "driver. Emissive calibration uses the NO filter.")
    elif driver == "kleink10.c":
        add(None, "Black calibration is optional. Measure and Calibrate do not "
            "request a black reference.",
            "/* Can do a black cal, but not required */")
    elif driver in {"inst.c", *_CONTRIBUTED_DRIVERS}:
        add(None, "Measure and Calibrate do not request a manual reference "
            "calibration for this instrument.",
            "*n_cals = inst_calt_none;" if driver == "inst.c" else
            "if (needed) *needed = inst_calt_none;")
    return notes
