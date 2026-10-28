"""Frozen package failures checked against current production source.

Scripted probes run the final compiled functions; they do not operate instruments, read
calibration caches or validate optical calibration.
Mutated ArgyllCMS excerpts retain Graeme W. Gill's GPL-2.0-or-later terms.
"""

import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import argyll_native_check as native  # noqa: E402
from native_argyll import native_resync_proof as proof  # noqa: E402
from native_argyll import sanitizers  # noqa: E402
from native_argyll.proof_inputs import fixture_manifest  # noqa: E402
from native_argyll.spyder_calibration_slots import _function  # noqa: E402


@pytest.fixture(scope="module")
def composition(argyll_archive, tmp_path_factory):
    output = tmp_path_factory.mktemp("package-composition") / "build"
    source, report = native.prepare_with_proof_inputs(argyll_archive, output)
    stage = report["native_resync_repair"]
    before = {p: (output / stage["inputs"] / p).read_text() for p in stage["before_sha256"]}
    return source, before


def test_broad_production_proof_inputs_match_the_frozen_manifest(composition):
    source, before = composition
    expected = fixture_manifest()["proofs"]["native_resync"]
    assert set(before) == set(expected)
    for path, entry in expected.items():
        assert native.digest(before[path].encode()) == entry["sha256"], path
        assert (source / path).is_file(), path


def test_each_fix_fails_before_and_passes_after_sanitized(composition, tmp_path):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, before = composition
    result = proof.prove(source, before, tmp_path / "proof", sanitize=True,
                         groups=proof.PACKAGE_GROUPS)
    results = result["results"]
    assert result["status"] == "passed" and len(results) == 38
    compiled = {name: r for name, r in results.items() if "returncode" in r}
    assert len(compiled) == 24 and all(
        r["returncode"] == (1 if name.startswith("baseline/") else 0)
        for name, r in compiled.items())
    # Ten dark blocks, as the package counts them; four tested white callers.
    assert len(results["baseline/dark"]["uninvalidated"]) == 10
    assert results["repaired/dark"]["uninvalidated"] == []
    assert results["baseline/white-callers"]["tested"] == 0
    assert results["repaired/white-callers"] == {"calls": 4, "tested": 4}
    assert len(results["baseline/specbos-range"]["problems"]) == 5
    assert results["repaired/specbos-range"]["problems"] == []
    assert len(results["baseline/i1pro3-restore"]["problems"]) == 2
    assert results["repaired/i1pro3-restore"]["problems"] == []
    assert len(results["baseline/restore-bounds"]["problems"]) == 10
    assert results["baseline/munki-timing"] == {"guard": False}
    assert results["repaired/restore-bounds"]["problems"] == []


PRO_DARK = "\t\t\ts->dark_valid = 0;\t\t/* Dark cal. data will be overwritten */\n"
MUNKI_REFLECTIVE_DARK = "\t" + PRO_DARK		# One block deeper
I1PRO, I1PRO3, MUNKI = "spectro/i1pro_imp.c", "spectro/i1pro3_imp.c", "spectro/munki_imp.c"
SPECBOS = "spectro/specbos.c"
DTP41 = "spectro/dtp41.c"

# id: (group, probe that must fail, path, actual source snippet, faulty replacement).
# The snippets select one production site directly. Each fails for its own guard:
# a dark write left valid, a
# non-finite or overflowed average accepted, a factor that is not finite and
# positive kept, a refusal a caller ignores, an out-of-range count, or a
# division by a zero margin.
MUTATIONS = {'pro_dark': ('dark',
              'dark',
              'spectro/i1pro_imp.c',
              '\t\t\ta1logd(p->log,2,"\\nDoing emissive (flash) black calibration with dcaltime '
              '%f, int_time %f, nummeas %d, gainmode %d\\n", s->dcaltime, s->inttime, nummeas, '
              's->gainmode);\n'
              '\t\t\ts->dark_valid = 0;\t\t/* Dark cal. data will be overwritten */\n'
              '\t\t\tstm = msec_time();\n',
              '\t\t\ta1logd(p->log,2,"\\nDoing emissive (flash) black calibration with dcaltime '
              '%f, int_time %f, nummeas %d, gainmode %d\\n", s->dcaltime, s->inttime, nummeas, '
              's->gainmode);\n'
              '\t\t\tstm = msec_time();\n'),
 'munki_reflective_dark': ('dark',
                           'dark',
                           'spectro/munki_imp.c',
                           '\t\t\t\ts->dark_valid = 0;\t\t/* Dark cal. data will be overwritten '
                           '*/\n'
                           '\t\t\t\tif ((ev = munki_dark_measure(p, s->dark_data, nummeas, '
                           '&s->inttime, s->gainmode))\n',
                           '\t\t\t\tif ((ev = munki_dark_measure(p, s->dark_data, nummeas, '
                           '&s->inttime, s->gainmode))\n'),
 'pro_cell_finite': ('average',
                     'average-i1pro',
                     'spectro/i1pro_imp.c',
                     '\tfor (j = -1; j < 128; j++) {\n'
                     '\t\tavg[j] /= (double)nummeas;\n'
                     '\t\tif (!isfinite(avg[j]))\n'
                     '\t\t\trv |= 1;\n'
                     '\t}\n',
                     '\tfor (j = -1; j < 128; j++) \n\t\tavg[j] /= (double)nummeas;\n'),
 'pro_undefined_ratio': ('average',
                         'average-i1pro',
                         'spectro/i1pro_imp.c',
                         '\t/* (Written so that an undefined ratio fails) */\n'
                         '\tif (!((maxavg - minavg)/norm <= PATCH_CONS_THR)) {\n',
                         '\tif ((maxavg - minavg)/norm > PATCH_CONS_THR) {\n'),
 'munki_cell_finite': ('average',
                       'average-munki',
                       'spectro/munki_imp.c',
                       '\tfor (j = -1; j < m->nraw; j++) {\n'
                       '\t\tavg[j] /= (double)nummeas;\n'
                       '\t\tif (!isfinite(avg[j]))\n'
                       '\t\t\trv |= 1;\n'
                       '\t}\n',
                       '\tfor (j = -1; j < m->nraw; j++) \n\t\tavg[j] /= (double)nummeas;\n'),
 'munki_undefined_ratio': ('average',
                           'average-munki',
                           'spectro/munki_imp.c',
                           '\t/* (Written so that an undefined ratio fails) */\n'
                           '\tif (!((maxavg - minavg)/norm <= PATCH_CONS_THR)) {\n',
                           '\tif ((maxavg - minavg)/norm > PATCH_CONS_THR) {\n'),
 'pro3_raw_finite': ('average',
                     'average-i1pro3',
                     'spectro/i1pro3_imp.c',
                     '\taavgd = aavg = vect_avg(avgs, nummeas);\n'
                     '\tif (!isfinite(aavg)) {\n'
                     '\t\tfree_dvector(avgs, 0, nummeas-1);\n'
                     '\t\treturn 1;\n'
                     '\t}\n'
                     '\n',
                     '\taavgd = aavg = vect_avg(avgs, nummeas);\n\n'),
 'factor1': ('white_factors',
             'white-factors',
             'spectro/munki_imp.c',
             '\tfor (j = 0; j < m->nwav1; j++)\n'
             '\t\tif (!isfinite(cal_factor1[j]) || cal_factor1[j] <= 0.0)\n'
             '\t\t\treturn -1;\n'
             '\n',
             '\n'),
 'factor2': ('white_factors',
             'white-factors',
             'spectro/munki_imp.c',
             '\tfor (j = 0; j < m->nwav2; j++)\n'
             '\t\tif (!isfinite(cal_factor2[j]) || cal_factor2[j] <= 0.0)\n'
             '\t\t\treturn -1;\n'
             '#endif /* HIGH_RES */\n',
             '#endif /* HIGH_RES */\n'),
 'transmission_caller': ('white_factors',
                         'white-callers',
                         'spectro/munki_imp.c',
                         '\t\t\t\tif ((warn = munki_compute_white_cal(p, s->cal_factor1, NULL, '
                         's->cal_factor1,\n'
                         '\t\t\t\t                                    s->cal_factor2, NULL, '
                         's->cal_factor2)) < 0) {\n'
                         '\t\t\t\t\tm->mmode = mmode;           /* Restore actual mode */\n'
                         '\t\t\t\t\treturn MUNKI_RD_WHITEREFERROR;\n'
                         '\t\t\t\t}\n'
                         '\t\t\t\tm->transwarn |= warn;\n',
                         '\t\t\t\tm->transwarn |= munki_compute_white_cal(p, s->cal_factor1, '
                         'NULL, s->cal_factor1,\n'
                         '\t\t\t\t                                       s->cal_factor2, NULL, '
                         's->cal_factor2);\n'),
 'count_limit': ('polarized',
                 'polarized',
                 'spectro/i1pro3_imp.c',
                 '\t\tif (maxval != 0.0)\n'
                 '\t\t\tinttime = inttime * s->targoscale * m->sens_target/maxval;\n'
                 '\n'
                 '\t\t/* Limit it before converting to a count */\n'
                 '\t\tif (maxval == 0.0 || inttime > 10.0 * m->min_int_time)\n'
                 '\t\t\tinttime = 10.0 * m->min_int_time;\n'
                 '\t\telse if (!(inttime >= 0.0))\n'
                 '\t\t\tinttime = 0.0;\n',
                 '\t\tinttime = inttime * s->targoscale * m->sens_target/maxval;\n'),
 'nan_floor': ('polarized',
               'polarized',
               'spectro/i1pro3_imp.c',
               '\t\telse if (!(inttime >= 0.0))\n\t\t\tinttime = 0.0;\n\t\t\n',
               '\t\t\n'),
 'zero_margin': ('agreement',
                 'agreement',
                 'spectro/munki_imp.c',
                 '\tif (lead_avg == 0.0 || trail_avg == 0.0\n'
                 '\t || !(lead_avg/trail_avg >= 0.7 && trail_avg/lead_avg >= 0.7)) {\n',
                 '\tif (!(lead_avg/trail_avg >= 0.7 && trail_avg/lead_avg >= 0.7)) {\n'),
 'completion_dark_valid': ('dark_completion',
                           'dark-completion',
                           'spectro/munki_imp.c',
                           '\t\t\t\ts->dark_valid = 1;\n\t\t\t\ts->want_dcalib = 0;\n',
                           '\t\t\t\ts->want_dcalib = 0;\n'),
 'completion_want_dcalib': ('dark_completion',
                            'dark-completion',
                            'spectro/munki_imp.c',
                            '\t\t\t\ts->want_dcalib = 0;\n\t\t\t\ts->ddate = cdate;\n',
                            '\t\t\t\ts->ddate = cdate;\n'),
 'completion_ddate': ('dark_completion',
                      'dark-completion',
                      'spectro/munki_imp.c',
                      '\t\t\t\ts->ddate = cdate;\n\t\t\t\ts->dark_int_time = s->inttime;\n',
                      '\t\t\t\ts->dark_int_time = s->inttime;\n'),
 'completion_dark_int_time': ('dark_completion',
                              'dark-completion',
                              'spectro/munki_imp.c',
                              '\t\t\t\ts->dark_int_time = s->inttime;\n'
                              '\t\t\t\ts->dark_gain_mode = s->gainmode;\n',
                              '\t\t\t\ts->dark_gain_mode = s->gainmode;\n'),
 'gain_low_dark_invalid': ('gain_switch',
                           'gain-switch',
                           'spectro/i1pro_imp.c',
                           '\t\t\t\t\ts->gainmode = 0;\n'
                           '\t\t\t\t\ts->dark_valid = 0;\t\t/* Dark cal. data is for the other '
                           'gain mode */\n'
                           '\t\t\t\t\tgainswitch = 1;\n',
                           '\t\t\t\t\ts->gainmode = 0;\n\t\t\t\t\tgainswitch = 1;\n'),
 'gain_low_flag': ('gain_switch',
                   'gain-switch',
                   'spectro/i1pro_imp.c',
                   '\t\t\t\t\tgainswitch = 1;\n'
                   '\t\t\t\t\t/* Measure white again with low gain */\n',
                   '\t\t\t\t\t/* Measure white again with low gain */\n'),
 'gain_remeasure': ('gain_switch',
                    'gain-switch',
                    'spectro/i1pro_imp.c',
                    "\t\t\t\t/* A white measured after a gain switch used the other gain's dark "
                    '*/\n'
                    '\t\t\t\tif (gainswitch) {\n'
                    '\t\t\t\t\tnummeas = i1pro_comp_nummeas(p, s->wcaltime, s->inttime);\n'
                    '\t\t\t\t\tif ((ev = i1pro_whitemeasure(p, s->cal_factor[0], '
                    's->cal_factor[1], s->white_data,\n'
                    '\t\t\t\t\t   &scale, nummeas, &s->inttime, s->gainmode, 1.0, 0)) != '
                    'I1PRO_OK) {\n'
                    '\t\t\t\t\t\tm->mmode = mmode;\t\t\t/* Restore actual mode */\n'
                    '\t\t\t\t\t\treturn ev;\n'
                    '\t\t\t\t\t}\n'
                    '\t\t\t\t}\n'
                    '\t\t\t\t/* (Judged on the final white) */\n'
                    '\t\t\t\tif (scale > 6.0) {\n'
                    '\t\t\t\t\tm->transwarn |= 2;\n'
                    '\t\t\t\t\ta1logd(p->log,2, "scan white reference is not bright enough by '
                    '%f\\n",scale);\n'
                    '\t\t\t\t}\n'
                    '\t\t\t}\n',
                    '\t\t\t}\n'),
 'gain_warning_place': ('gain_switch',
                        'gain-switch',
                        'spectro/i1pro_imp.c',
                        '\t\t\t\ta1logd(p->log,2,"After scan gain adaption scale = '
                        '%f\\n",scale);\n'
                        '\t\n',
                        '\t\t\t\ta1logd(p->log,2,"After scan gain adaption scale = '
                        '%f\\n",scale);\n'
                        '\t\t\t\tif (scale > 6.0) {\n'
                        '\t\t\t\t\tm->transwarn |= 2;\n'
                        '\t\t\t\t\ta1logd(p->log,2, "scan white reference is not bright enough by '
                        '%f\\n",scale);\n'
                        '\t\t\t\t}\n'
                        '\t\n'),
 'specbos_para_site': ('specbos_range',
                       'specbos-range',
                       'spectro/specbos.c',
                       '\t\n'
                       '\t\ta1logd(p->log, 1, " Long wl range %f\\n",p->wl_long);\n'
                       '\n'
                       '\t\tif ((p->nbands = specbos_nbands(p->wl_short, p->wl_long)) == 0) {\n'
                       '\t\t\tamutex_unlock(p->lock);\n'
                       '\t\t\ta1loge(p->log, 1, "specbos_init_inst: unusable wavelength range %f '
                       '- %f\\n",\n'
                       '\t\t\t                                        p->wl_short, p->wl_long);\n'
                       '\t\t\treturn specbos_interp_code((inst *)p, SPECBOS_DATA_PARSE_ERROR);\n'
                       '\t\t}\n'
                       '\t\n'
                       '\t\t/* Set the wavelength range and resolution */\n'
                       '\t\tsprintf(mes, "*para:wran %d %d 1\\r", (int)(p->wl_short+0.5), '
                       '(int)(p->wl_long+0.5));\n',
                       '\t\n'
                       '\t\ta1logd(p->log, 1, " Long wl range %f\\n",p->wl_long);\n'
                       '\n'
                       '\t\tp->nbands = (int)((p->wl_long - p->wl_short + 1.0)/1.0 + 0.5);\n'
                       '\t\n'
                       '\t\t/* Set the wavelength range and resolution */\n'
                       '\t\tsprintf(mes, "*para:wran %d %d 1\\r", (int)(p->wl_short+0.5), '
                       '(int)(p->wl_long+0.5));\n'),
 'specbos_conf_site': ('specbos_range',
                       'specbos-range',
                       'spectro/specbos.c',
                       '\t\n'
                       '\t\ta1logd(p->log, 1, " Long wl range %f\\n",p->wl_long);\n'
                       '\n'
                       '\t\tif ((p->nbands = specbos_nbands(p->wl_short, p->wl_long)) == 0) {\n'
                       '\t\t\tamutex_unlock(p->lock);\n'
                       '\t\t\ta1loge(p->log, 1, "specbos_init_inst: unusable wavelength range %f '
                       '- %f\\n",\n'
                       '\t\t\t                                        p->wl_short, p->wl_long);\n'
                       '\t\t\treturn specbos_interp_code((inst *)p, SPECBOS_DATA_PARSE_ERROR);\n'
                       '\t\t}\n'
                       '\t\n'
                       '\t\t/* Set the wavelength range and resolution */\n'
                       '\t\tsprintf(mes, "*conf:wran %d %d 1\\r", (int)(p->wl_short+0.5), '
                       '(int)(p->wl_long+0.5));\n',
                       '\t\n'
                       '\t\ta1logd(p->log, 1, " Long wl range %f\\n",p->wl_long);\n'
                       '\n'
                       '\t\tp->nbands = (int)((p->wl_long - p->wl_short + 1.0)/1.0 + 0.5);\n'
                       '\t\n'
                       '\t\t/* Set the wavelength range and resolution */\n'
                       '\t\tsprintf(mes, "*conf:wran %d %d 1\\r", (int)(p->wl_short+0.5), '
                       '(int)(p->wl_long+0.5));\n'),
 'specbos_retry_site': ('specbos_range',
                        'specbos-range',
                        'spectro/specbos.c',
                        '\t\t\tif ((nbands = specbos_nbands(p->wl_short < 380.0 ? 380.0 : '
                        'p->wl_short,\n'
                        '\t\t\t                              p->wl_long > 780.0 ? 780.0 : '
                        'p->wl_long)) == 0) {\n'
                        '\t\t\t\tamutex_unlock(p->lock);\n'
                        '\t\t\t\treturn specbos_interp_code((inst *)p, ec);\n'
                        '\t\t\t}\n'
                        '\t\t\tif (p->wl_short < 380.0)\n',
                        '\t\t\tif (p->wl_short < 380.0)\n'),
 'specbos_int_reply': ('specbos_range',
                       'specbos-range',
                       'spectro/specbos.c',
                       '\t\t\tif (sscanf(buf, "%lf %lf %lf",&wstart,&wend,&wstep) != 3) {\n',
                       '\t\t\tif (sscanf(buf, "%d %d %d",&wstart,&wend,&wstep) != 3) {\n'),
 'i1pro3_timing_guard': ('i1pro3_timing',
                         'i1pro3-timing',
                         'spectro/i1pro3_imp.c',
                         "\t/* Refuse timing parameters that would overflow i1pro3_do_measure()'s "
                         '*/\n'
                         "\t/* int counts. min_int_time can't exceed MAX_INT_TIME, and a "
                         'MAXSCANTIME */\n'
                         '\t/* polarized scan at tint 10 has the most native measurements, each '
                         'of */\n'
                         '\t/* 2 * MX_NSEN bytes and 2 * 10 * minintclks zebra clocks. */\n'
                         '\t{\n'
                         '\t\tdouble mit = m->minintclks * m->intclkp;\t/* As '
                         'i1pro3_parse_eeprom() */\n'
                         '\t\tdouble nnmeas;\n'
                         '\n'
                         '\t\tif (!(mit > 0.0 && mit <= MAX_INT_TIME)) {\n'
                         '\t\t\ta1logd(p->log,1,"Minimum integration time %g is '
                         'unusable\\n",mit);\n'
                         '\t\t\treturn I1PRO3_DATA_PARSE_ERROR;\n'
                         '\t\t}\n'
                         '\n'
                         '\t\t/* nummeas * tint/2 + nnskip, as i1pro3_do_measure() */\n'
                         '\t\tnnmeas = (MAXSCANTIME/(10.0 * mit) + 0.5) * 10/2 + 5 * '
                         '((LEDTURNONMEAS + 4)/5);\n'
                         '\t\tif (2.0 * MX_NSEN * nnmeas > (double)INT_MAX\n'
                         '\t\t || 2.0 * 10 * m->minintclks * nnmeas > (double)INT_MAX) {\n'
                         '\t\t\ta1logd(p->log,1,"Integration clock %g x %u overflows a '
                         'scan\\n",m->intclkp,m->minintclks);\n'
                         '\t\t\treturn I1PRO3_DATA_PARSE_ERROR;\n'
                         '\t\t}\n'
                         '\t}\n'
                         '\n'
                         '/* Get EEPROM data from i1pro3_fake_eeprom.h */\n',
                         '/* Get EEPROM data from i1pro3_fake_eeprom.h */\n'),
 'i1pro3_restore_check': ('i1pro3_timing',
                          'i1pro3-restore',
                          'spectro/i1pro3_imp.c',
                          "\t/* A restored integration time outside what the mode's calibration "
                          'sets */\n'
                          '\t/* (or NaN) would overflow the measurement counts. Keep the initial '
                          'time */\n'
                          '\t/* and leave the mode uncalibrated, as for a missing calibration. '
                          '*/\n'
                          '\tfor (i = 0; i < i1p3_no_modes; i++) {\n'
                          '\t\ti1pro3_state *s = &m->ms[i];\n'
                          '\t\tdouble mintime = s->pol ? 2.0 * m->min_int_time : '
                          'm->min_int_time;\t/* As */\n'
                          '\t\tdouble maxtime = s->pol ? 10.0 * m->min_int_time\t\t\t\t/* '
                          'i1pro3_pol_refl_cal() */\n'
                          '\t\t               : itime[i] > m->max_int_time ? itime[i] : '
                          'm->max_int_time;\n'
                          '\n'
                          '\t\tif (!(s->inttime >= mintime && s->inttime <= maxtime)) {\n'
                          '\t\t\ta1logd(p->log,2,"Mode %d restored integration time %f is '
                          'unusable\\n",i,s->inttime);\n'
                          '\t\t\ts->inttime = itime[i];\n'
                          '\t\t\ts->dark_valid = 0;\n'
                          '\t\t\ts->cal_valid = 0;\n'
                          '\t\t}\n'
                          '\t}\n'
                          '\n'
                          '\ta1logd(p->log,3,"i1pro3_restore_calibration done OK\\n");\n',
                          '\ta1logd(p->log,3,"i1pro3_restore_calibration done OK\\n");\n'),
 'i1pro3_restore_initial': ('i1pro3_timing',
                            'i1pro3-restore',
                            'spectro/i1pro3_imp.c',
                            '\tfor (i = 0; i < i1p3_no_modes; i++)\n'
                            '\t\titime[i] = m->ms[i].inttime;\n'
                            '\n'
                            '\ti1pro3_nv_op(p, &x, fp);\n',
                            '\ti1pro3_nv_op(p, &x, fp);\n'),
 'pro_restore_range': ('restore_bounds',
                       'restore-bounds',
                       'spectro/i1pro_imp.c',
                       '\t\t && (!s->adaptive || (ts.inttime >= -DBL_MAX && ts.inttime <= '
                       'DBL_MAX\t/* Finite */\n'
                       '\t\t    && m->min_int_time > 0.0 && m->max_int_time >= m->min_int_time\n'
                       '\t\t    && m->max_int_time <= DBL_MAX))\t\t/* and a range to keep it in '
                       '*/\n'
                       '\t\t && ts.gainmode >= 0 && ts.gainmode <= maxgain\n'
                       '\t\t && ts.dark_gain_mode >= 0 && ts.dark_gain_mode <= maxgain\n'
                       '\t\t && (s->adaptive || !ts.dark_valid || ts.dark_gain_mode == '
                       'ts.gainmode)\n'
                       '\t\t) {\n',
                       '\t\t) {\n'),
 'pro_restore_clamp': ('restore_bounds',
                       'restore-bounds',
                       'spectro/i1pro_imp.c',
                       '\t\t\t/* An adaptive time can be just outside the range after rounding to '
                       '*/\n'
                       '\t\t\t/* clocks; keep it inside, as i1pro_trigger_one_measure() does a '
                       'low one */\n'
                       '\t\t\tif (s->adaptive) {\n'
                       '\t\t\t\tif (s->inttime < m->min_int_time)\n'
                       '\t\t\t\t\ts->inttime = m->min_int_time;\n'
                       '\t\t\t\telse if (s->inttime > m->max_int_time)\n'
                       '\t\t\t\t\ts->inttime = m->max_int_time;\n'
                       '\t\t\t}\n'
                       '\n',
                       '\n'),
 'pro_trigger_clocks': ('restore_bounds',
                        'restore-bounds',
                        'spectro/i1pro_imp.c',
                        '\t\tif (!(dintclocks <= 65535.0))\n',
                        '\t\tif (dintclocks > 65535.0)\n'),
 'munki_restore_range': ('restore_bounds',
                         'restore-bounds',
                         'spectro/munki_imp.c',
                         '\t\t && (!s->adaptive || (ts.inttime >= -DBL_MAX && ts.inttime <= '
                         'DBL_MAX\t/* Finite */\n'
                         '\t\t    && m->min_int_time > 0.0 && m->max_int_time >= m->min_int_time\n'
                         '\t\t    && m->max_int_time <= DBL_MAX))\t\t/* and a range to keep it in '
                         '*/\n'
                         '\t\t && ts.gainmode >= 0 && ts.gainmode <= s->auto_gain\t/* High gain '
                         'only if automatic */\n'
                         '\t\t && ts.dark_gain_mode >= 0 && ts.dark_gain_mode <= s->auto_gain\n'
                         '\t\t && (s->adaptive || !ts.dark_valid || ts.dark_gain_mode == '
                         'ts.gainmode)\n'
                         '\t\t) {\n',
                         '\t\t) {\n'),
 'munki_restore_clamp': ('restore_bounds',
                         'restore-bounds',
                         'spectro/munki_imp.c',
                         '\t\t\t/* An adaptive time can be just outside the range after rounding '
                         'to */\n'
                         '\t\t\t/* clocks; keep it inside the range calibration uses */\n'
                         '\t\t\tif (s->adaptive) {\n'
                         '\t\t\t\tif (s->inttime < m->min_int_time)\n'
                         '\t\t\t\t\ts->inttime = m->min_int_time;\n'
                         '\t\t\t\telse if (s->inttime > m->max_int_time)\n'
                         '\t\t\t\t\ts->inttime = m->max_int_time;\n'
                         '\t\t\t}\n'
                         '\t\t\ts->dark_valid = ts.dark_valid;\n',
                         '\t\t\ts->dark_valid = ts.dark_valid;\n'),
 'munki_trigger_clocks': ('restore_bounds',
                          'restore-bounds',
                          'spectro/munki_imp.c',
                          '\tif (!(dintclocks <= (double)INT_MAX))\t\t/* (Including NaN) */\n'
                          '\t\treturn MUNKI_INT_INTTOOBIG;\n'
                          '\tif (dintclocks < (double)INT_MIN)\n'
                          '\t\treturn MUNKI_INT_INTTOOSMALL;\n'
                          '\tintclocks = (int)dintclocks;\n',
                          '\tintclocks = (int)dintclocks;\n'),
 'pro_restore_dark_pairing': ('restore_bounds',
                              'restore-bounds',
                              'spectro/i1pro_imp.c',
                              '\t\t && (s->adaptive || !ts.dark_valid || ts.dark_gain_mode == '
                              'ts.gainmode)\n'
                              '\t\t) {\n',
                              '\t\t) {\n'),
 'munki_restore_dark_pairing': ('restore_bounds',
                                'restore-bounds',
                                'spectro/munki_imp.c',
                                '\t\t && (s->adaptive || !ts.dark_valid || ts.dark_gain_mode == '
                                'ts.gainmode)\n'
                                '\t\t) {\n',
                                '\t\t) {\n'),
 'munki_timing_guard': ('munki_timing',
                        'munki-timing',
                        'spectro/munki_imp.c',
                        "\t/* Refuse timing values the driver can't use: a tick or minimum count "
                        '*/\n'
                        "\t/* that isn't positive, a minimum above the maximum, a calibration "
                        'time */\n'
                        "\t/* that isn't a positive finite time, or values that would overflow "
                        'the */\n'
                        '\t/* measurement counts and buffer sizes. Integration times are no '
                        'shorter */\n'
                        '\t/* than the minimum or the calibration time, and a reading lasts no '
                        'longer */\n'
                        '\t/* than a scan, the calibration scan or the LED pre-heat, after the '
                        '*/\n'
                        '\t/* invalid samples. */\n'
                        '\t{\n'
                        '\t\tdouble tmin = m->cal_int_time < m->min_int_time ? m->cal_int_time : '
                        'm->min_int_time;\n'
                        '\t\tdouble tmax = MAXSCANTIME, nmeas;\n'
                        '\n'
                        '\t\tif (m->calscantime > tmax)\n'
                        '\t\t\ttmax = m->calscantime;\n'
                        '\t\tif (m->ledpreheattime > tmax)\n'
                        '\t\t\ttmax = m->ledpreheattime;\n'
                        '\t\tif (!(m->tickdur > 0 && m->minintcount > 0 && m->min_int_time <= '
                        'm->max_int_time\n'
                        '\t\t   && m->cal_int_time > 0.0 && m->cal_int_time <= DBL_MAX)) {\n'
                        '\t\t\ta1logd(p->log,1,"Timing values unusable: tick %d x %d, cal. int. '
                        'time %g\\n",\n'
                        '\t\t\t                                 m->tickdur, m->minintcount, '
                        'm->cal_int_time);\n'
                        '\t\t\treturn MUNKI_DATA_RANGE;\n'
                        '\t\t}\n'
                        '\t\tnmeas = ceil(m->refinvalidsampt/tmin) + tmax/tmin + 0.5;\n'
                        '\t\tif (2.0 * NSEN_MAX * nmeas > (double)INT_MAX) {\n'
                        '\t\t\ta1logd(p->log,1,"Integration time %g overflows a %g sec. '
                        'reading\\n",tmin,tmax);\n'
                        '\t\t\treturn MUNKI_DATA_RANGE;\n'
                        '\t\t}\n'
                        '\t}\n'
                        '\n'
                        '#ifdef USE_THREAD\n',
                        '#ifdef USE_THREAD\n'),
 'dtp41_sample_activation': ('dtp41',
                             'dtp41',
                             'spectro/dtp41.c',
                             '\tp->lastmode = (p->lastmode & ~inst_mode_sub_mask) | '
                             'inst_mode_spot;\n'
                             '\tif ((ev = activate_mode(p)) != inst_ok)\n'
                             '\t\treturn ev;\n',
                             '\tp->lastmode = (p->lastmode & ~inst_mode_sub_mask) | '
                             'inst_mode_spot;\n'
                             '\tactivate_mode(p);\n'),
 'dtp41_strip_activation': ('dtp41',
                            'dtp41',
                            'spectro/dtp41.c',
                            '\t/* Build the strip before changing the instrument, so that a value '
                            '*/\n'
                            "\t/* that doesn't fit is refused with nothing sent */\n"
                            '\tif (build_strip(p, tbuf, name, npatch, pname, sguide, pwid, gwid, '
                            'twid) != 0)\n'
                            '\t\treturn inst_bad_parameter;\n'
                            '\n'
                            '\t/* Configure for dynamic mode */\n'
                            '\tp->lastmode = (p->lastmode & ~inst_mode_sub_mask) | '
                            'inst_mode_strip;\n'
                            '\tif ((ev = activate_mode(p)) != inst_ok)\n'
                            '\t\treturn ev;\n',
                            '\t/* Configure for dynamic mode */\n'
                            '\tp->lastmode = (p->lastmode & ~inst_mode_sub_mask) | '
                            'inst_mode_strip;\n'
                            '\tactivate_mode(p);\n'
                            '\n'
                            '\tbuild_strip(p, tbuf, name, npatch, pname, sguide, pwid, gwid, '
                            'twid);\n'),
 'dtp41_unknown_mode': ('dtp41',
                        'dtp41',
                        'spectro/dtp41.c',
                        '\tp->mode = inst_mode_none;\t/* Unknown, so set both again next time */\n'
                        '\treturn rv;\n',
                        '\treturn rv;\n'),
 'dtp41_field_range': ('dtp41',
                       'dtp41',
                       'spectro/dtp41.c',
                       '\tif (!(npatch >= 1 && npatch <= 999\n'
                       '\t   && pwid >= 0.0 && pwid < 100.0 && gwid >= 0.0 && gwid < 100.0))\n'
                       '\t\treturn 1;\n'
                       '\tif (pwid == 0.0)\t\t/* (-0.0 would print a sign) */\n',
                       '\tif (pwid == 0.0)\t\t/* (-0.0 would print a sign) */\n'),
 'dtp41_patch_rounding': ('dtp41',
                          'dtp41',
                          'spectro/dtp41.c',
                          '\tif (sprintf(tp, "%05.2f",pwid) != 5)\t\t/* Rounded up to 100.00 */\n'
                          '\t\treturn 1;\n',
                          '\tsprintf(tp, "%05.2f",pwid);\n'),
 'dtp41_gap_rounding': ('dtp41',
                        'dtp41',
                        'spectro/dtp41.c',
                        '\tif (sprintf(tp, "%05.2f",gwid) != 5)\n\t\treturn 1;\n',
                        '\tsprintf(tp, "%05.2f",gwid);\n'),
 'dtp41_signed_zero': ('dtp41',
                       'dtp41',
                       'spectro/dtp41.c',
                       '\tif (pwid == 0.0)\t\t/* (-0.0 would print a sign) */\n'
                       '\t\tpwid = 0.0;\n'
                       '\tif (gwid == 0.0)\n',
                       '\tif (gwid == 0.0)\n'),
 'dtp41_trailer_sign': ('dtp41',
                        'dtp41',
                        'spectro/dtp41.c',
                        '\t\tif (!(twid >= 0.0))\n\t\t\treturn 1;\n\t\tif (twid == 0.0)\n',
                        '\t\tif (twid == 0.0)\n'),
 'dtp41_trailer_requested': ('dtp41',
                             'dtp41',
                             'spectro/dtp41.c',
                             '\tif ((p->lastmode & inst_mode_illum_mask) == '
                             'inst_mode_transmission) {\t/* As requested */\n'
                             '\n'
                             '\t\tif (!(twid >= 0.0))\n'
                             '\t\t\treturn 1;\n'
                             '\t\tif (twid == 0.0)\n'
                             '\t\t\ttwid = 0.0;\n',
                             '\tif ((p->mode & inst_mode_illum_mask) == inst_mode_transmission) '
                             '{\n'
                             '\n'),
 'dtp41_calibration_needs': ('dtp41',
                             'dtp41',
                             'spectro/dtp41.c',
                             '\t\t\n'
                             '\tif ((p->lastmode & inst_mode_illum_mask) == '
                             'inst_mode_transmission) {\t/* As requested */\n',
                             '\t\t\n'
                             '\tif ((p->mode & inst_mode_illum_mask) == inst_mode_transmission) '
                             '{\n'),
 'dtp41_calibration': ('dtp41',
                       'dtp41',
                       'spectro/dtp41.c',
                       '\tif ((p->lastmode & inst_mode_illum_mask) == inst_mode_transmission) {\t'
                       '/* As requested */\n'
                       '\t\tif (*calt & inst_calt_trans_white) {\n',
                       '\tif ((p->mode & inst_mode_illum_mask) == inst_mode_transmission) {\n'
                       '\t\tif (*calt & inst_calt_trans_white) {\n'),
 'completion_dark_gain_mode': ('dark_completion',
                               'dark-completion',
                               'spectro/munki_imp.c',
                               '\t\t\t\ts->dark_gain_mode = s->gainmode;\n\t\n',
                               '\t\n')}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_package_guard_is_needed(composition, tmp_path, name):
    group, probe, path, anchor, replacement = MUTATIONS[name]
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, before = composition
    original = (source / path).read_text()
    assert original.count(anchor) == 1, "Mutation must select exactly one production site"
    assert anchor != replacement, "Mutation must alter the input"
    mutated = tmp_path / "source"
    # The proof reads these upstream headers for the code tables of every group.
    support = ("spectro/inst.h", "spectro/insttypes.h", "spectro/i1pro_imp.h",
               "spectro/i1pro3_imp.h", "spectro/munki_imp.h", "spectro/dtp92.h")
    for filename in (*before, *support):
        (mutated / filename).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / filename, mutated / filename)
    (mutated / path).write_text(original.replace(anchor, replacement, 1))
    with pytest.raises(AssertionError, match=f"Resync behavior differs at repaired/{probe}[:;]"):
        proof.prove(mutated, before, tmp_path / "mutant", groups=(group,))
    assert json.loads((tmp_path / "mutant/report.json").read_text())["status"] == "incomplete"


def test_the_dark_census_reads_enclosing_blocks_not_sibling_branches(composition):
    """The white loop's saturated branch clears dark_valid textually before the
    reflective re-black; that branch does not run on every path to it."""
    source, before = composition
    texts = {p: (source / p).read_text() for p in before}
    assert proof._dark_census(texts) == []
    texts["spectro/munki_imp.c"] = texts["spectro/munki_imp.c"].replace(
        MUNKI_REFLECTIVE_DARK, "", 1)
    (missing,) = proof._dark_census(texts)
    assert missing.startswith("munki line ")


@pytest.mark.parametrize("placement", ["before_dark", "after_white"])
def test_dark_state_is_published_only_after_its_own_success(
    composition, tmp_path, placement
):
    source, before = composition
    text = (source / MUNKI).read_text()
    state = "".join(
        "\t\t\t\ts->" + field + " = " + value + ";\n"
        for field, value in (
            ("dark_valid", "1"),
            ("want_dcalib", "0"),
            ("ddate", "cdate"),
            ("dark_int_time", "s->inttime"),
            ("dark_gain_mode", "s->gainmode"),
        )
    )
    assert text.count(state) == 1
    text = text.replace(state, "", 1)
    if placement == "before_dark":
        point = (
            "\t\t\t\tif ((ev = munki_dark_measure(p, s->dark_data, nummeas, "
            "&s->inttime, s->gainmode))\n"
        )
        # Publish after the call changes inttime, but before checking its result.
        premature = (
            "\t\t\t\tif ((ev = munki_dark_measure(p, s->dark_data, nummeas, "
            "&s->inttime, s->gainmode),\n"
            "\t\t\t\t     s->dark_valid = 1, s->want_dcalib = 0,\n"
            "\t\t\t\t     s->ddate = cdate, s->dark_int_time = s->inttime,\n"
            "\t\t\t\t     s->dark_gain_mode = s->gainmode, ev)\n"
        )
        assert text.count(point) == 1
        text = text.replace(point, premature, 1)
    else:
        anchor = "\t\t\ts->cal_valid = 1;\n\t\t\ts->cfdate = cdate;"
        assert text.count(anchor) == 1
        text = text.replace(anchor, state + anchor, 1)
    mutated = tmp_path / "source"
    shutil.copytree(source, mutated)
    assert text != (source / MUNKI).read_text(), "Mutation must change the compiled source"
    (mutated / MUNKI).write_text(text)
    with pytest.raises(
        AssertionError, match="Resync behavior differs at repaired/dark-completion;"
    ):
        proof.prove(mutated, before, tmp_path / "mutant", groups=("dark_completion",))
    if placement == "before_dark":
        output = (tmp_path / "mutant/repaired/dark-completion/run.log").read_text()
        assert "success: code=" in output
        assert "FAIL successful" not in output
        assert "FAIL failed inner dark remains invalid" in output
        for field in ("pending flag", "date", "integration time", "gain mode"):
            assert f"FAIL failed inner dark {field} changed" in output


@pytest.mark.parametrize("weakened", [
    ("bands >= 2.0", "bands >= 1.0"),  # one band divides by zero in XSPECT_XWL
    ("bands < XSPECT_MAX_BANDS + 1.0", "bands < XSPECT_MAX_BANDS + 2.0"),  # 602 bands
    ("wl_short != (double)(int)(wl_short + 0.5)\n\t || ", ""),
    ("\n\t || wl_long != (double)(int)(wl_long + 0.5)", ""),
])
def test_each_specbos_band_condition_is_needed(composition, tmp_path, weakened):
    """The probe must refuse a helper with any one of its conditions weakened."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, _ = composition
    function = _function((source / SPECBOS).read_text(), "specbos_nbands")
    old, new = weakened
    assert function.count(old) == 1
    folder = tmp_path / "probe"
    folder.mkdir()
    (folder / "function.inc").write_text(function.replace(old, new))
    (folder / "command.inc").write_text(proof._specbos_commands((source / SPECBOS).read_text()))
    (folder / "codes.inc").write_text("#define XSPECT_MAX_BANDS 601\n")
    built = subprocess.run(["cc", "-std=gnu99", "-O1", f"-I{folder}", "-o", str(folder / "probe"),
                            str(ROOT / "tools/native_argyll/native_resync_specbos_probe.c"), "-lm"],
                           capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(folder / "probe")], capture_output=True, text=True, timeout=10)
    assert ran.returncode == 1 and "FAIL" in ran.stdout


@pytest.mark.parametrize("weakened", [
    ("mit > 0.0 && mit <= MAX_INT_TIME", "mit > 0.0"),
    ("2.0 * MX_NSEN * nnmeas > (double)INT_MAX\n\t\t || ", ""),
    ("\n\t\t || 2.0 * 10 * m->minintclks * nnmeas > (double)INT_MAX", ""),
])
def test_each_i1pro3_timing_condition_is_needed(composition, tmp_path, weakened):
    """The probe must refuse the init guard with any one condition weakened."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, _ = composition
    text = (source / I1PRO3).read_text()
    start = text.index("\t/* Refuse timing parameters that would overflow")
    block = text[start:]
    block = block[:block.index("\n\t}\n", block.index("overflows a scan")) + len("\n\t}\n")]
    old, new = weakened
    assert block.count(old) == 1
    header = (source / "spectro/i1pro3_imp.h").read_text()
    codes = "typedef int i1pro3_code;\n" + "".join(
        proof._define(header, n) for n in ("I1PRO3_OK", "I1PRO3_DATA_PARSE_ERROR"))
    codes += "".join(proof._define(text, n) for n in
                     ("MAX_INT_TIME", "MAXSCANTIME", "MX_NSEN", "LEDTURNONMEAS"))
    folder = tmp_path / "probe"
    folder.mkdir()
    (folder / "block.inc").write_text(block.replace(old, new))
    (folder / "codes.inc").write_text(codes)
    built = subprocess.run(["cc", "-std=gnu99", "-O1", f"-I{folder}", "-o", str(folder / "probe"),
                            str(ROOT / "tools/native_argyll/native_resync_i1pro3_timing_probe.c"),
                            "-lm"], capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(folder / "probe")], capture_output=True, text=True, timeout=30)
    assert ran.returncode == 1 and "FAIL" in ran.stdout


@pytest.mark.parametrize("weakened", [
    ("s->inttime >= mintime && ", ""),
    ("s->pol ? 2.0 * m->min_int_time : m->min_int_time;", "m->min_int_time;"),
    ("s->pol ? 10.0 * m->min_int_time\t\t\t\t/* i1pro3_pol_refl_cal() */\n\t\t               : ",
     ""),
    ("itime[i] > m->max_int_time ? itime[i] : m->max_int_time", "m->max_int_time"),
    ("\t\t\ts->inttime = itime[i];\n", ""),
    ("\t\t\ts->dark_valid = 0;\n", ""),
    ("\t\t\ts->cal_valid = 0;\n", ""),
])
def test_each_i1pro3_restore_condition_is_needed(composition, tmp_path, weakened):
    """The probe must refuse the restore check with any one condition weakened."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, _ = composition
    text = (source / I1PRO3).read_text()
    fn = proof._function(text, "i1pro3_restore_calibration")
    check = fn[fn.index(proof.I1PRO3_RESTORE_CHECK):]
    check = check[:check.index("\n\t}\n", check.index("s->cal_valid = 0;")) + len("\n\t}\n")]
    guard = text[text.index("\t/* Refuse timing parameters that would overflow"):]
    guard = guard[:guard.index("\n\t}\n", guard.index("overflows a scan")) + len("\n\t}\n")]
    old, new = weakened
    assert check.count(old) == 1
    header = (source / "spectro/i1pro3_imp.h").read_text()
    codes = "typedef int i1pro3_code;\n" + "".join(
        proof._define(header, n) for n in ("I1PRO3_OK", "I1PRO3_DATA_PARSE_ERROR"))
    codes += "".join(proof._define(text, n) for n in
                     ("MAX_INT_TIME", "MAXSCANTIME", "MX_NSEN", "LEDTURNONMEAS"))
    folder = tmp_path / "probe"
    folder.mkdir()
    (folder / "block.inc").write_text(check.replace(old, new))
    (folder / "guard.inc").write_text(guard)
    (folder / "codes.inc").write_text(codes)
    built = subprocess.run(["cc", "-std=gnu99", "-O1", f"-I{folder}", "-o", str(folder / "probe"),
                            str(ROOT / "tools/native_argyll/native_resync_i1pro3_restore_probe.c"),
                            "-lm"], capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(folder / "probe")], capture_output=True, text=True, timeout=30)
    assert ran.returncode == 1 and "FAIL" in ran.stdout


RESTORE_RANGE = ("\t\t && (!s->adaptive || (ts.inttime >= -DBL_MAX && ts.inttime <= DBL_MAX"
                 "\t/* Finite */\n"
                 "\t\t    && m->min_int_time > 0.0 && m->max_int_time >= m->min_int_time\n"
                 "\t\t    && m->max_int_time <= DBL_MAX))")
# family: the restored gain mode's upper limit, weakened to accept any value
WEAK_GAIN = {"i1pro": ("ts.gainmode <= maxgain", "1"),
             "munki": ("ts.gainmode <= s->auto_gain", "1")}
WEAK_DARK_GAIN = {"i1pro": ("ts.dark_gain_mode <= maxgain", "1"),
                  "munki": ("ts.dark_gain_mode <= s->auto_gain", "1")}
# family: the trigger weakened to accept NaN, or without its lower bound
WEAK_TRIGGER = {
    "i1pro": ("!(dintclocks <= 65535.0)", "(dintclocks > 65535.0)"),
    "munki": ("\tif (dintclocks < (double)INT_MIN)\n\t\treturn MUNKI_INT_INTTOOSMALL;\n", ""),
}


@pytest.mark.parametrize("family", ["i1pro", "munki"])
@pytest.mark.parametrize("block, old, new", [
    ("condition.inc", "ts.inttime >= -DBL_MAX && ", ""),
    ("condition.inc", " && ts.inttime <= DBL_MAX", ""),
    ("condition.inc", "m->min_int_time > 0.0 && ", ""),
    ("condition.inc", " && m->max_int_time >= m->min_int_time", ""),
    ("condition.inc", "\n\t\t    && m->max_int_time <= DBL_MAX))", "))"),
    ("clamp.inc", "\t\t\t\ts->inttime = m->min_int_time;\n", "\t\t\t\t;\n"),
    ("clamp.inc", "\t\t\t\ts->inttime = m->max_int_time;\n", "\t\t\t\t;\n"),
    ("condition.inc", "ts.gainmode >= 0 && ", ""),
    ("condition.inc", "ts.dark_gain_mode >= 0 && ", ""),
    ("condition.inc", "gain", None),
    ("condition.inc", "dark gain", None),
    ("condition.inc", "\n" + proof.DARK_PAIRING.rstrip("\n"), ""),
    ("trigger.inc", None, None),
])
def test_each_restored_time_condition_is_needed(composition, tmp_path, family, block, old, new):
    """The sanitized probe must refuse the restore or trigger with any one
    condition weakened: an unchecked NaN or out-of-range conversion traps."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, _ = composition
    path = I1PRO if family == "i1pro" else MUNKI
    blocks = proof._restore_bounds_blocks(family, (source / path).read_text())
    assert RESTORE_RANGE in blocks["condition.inc"]
    if new is None:
        old, new = {"gain": WEAK_GAIN, "dark gain": WEAK_DARK_GAIN, None: WEAK_TRIGGER}[old][family]
    assert blocks[block].count(old) == 1, (block, old)
    blocks[block] = blocks[block].replace(old, new)
    prefix = family.upper()
    header = (source / f"spectro/{family}_imp.h").read_text()
    codes = "".join(proof._define(header, f"{prefix}_{n}") for n in
                    ("OK", "INT_INTTOOBIG", "INT_INTTOOSMALL"))
    codes += (f"#define CODE_OK {prefix}_OK\n#define CODE_TOOBIG {prefix}_INT_INTTOOBIG\n"
              f"#define CODE_TOOSMALL {prefix}_INT_INTTOOSMALL\n")
    folder = tmp_path / "probe"
    folder.mkdir()
    for name, text in {**blocks, "codes.inc": codes}.items():
        (folder / name).write_text(text)
    (folder / "probe.c").write_text(f"#define FAMILY {1 if family == 'i1pro' else 2}\n"
        + (ROOT / "tools/native_argyll/native_resync_restore_probe.c").read_text())
    built = subprocess.run(["cc", "-std=gnu99", "-O1", "-fsanitize=undefined",
                            "-fno-sanitize-recover=all", f"-I{folder}", "-o", str(folder / "probe"),
                            str(folder / "probe.c"), "-lm"],
                           capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(folder / "probe")], capture_output=True, text=True, timeout=30)
    assert ran.returncode != 0 and ("FAIL" in ran.stdout or "runtime error" in ran.stderr)


@pytest.mark.parametrize("family, path", [("i1pro", I1PRO), ("munki", MUNKI)])
def test_restore_census_names_a_missing_dark_pairing(composition, family, path):
    """Removing the dark-gain pairing from a restore is reported as
    that defect, not as some other census problem."""
    source, _ = composition
    text = (source / path).read_text()
    assert text.count(proof.DARK_PAIRING) == 1
    assert proof._restore_bounds_census(family, text) == []
    assert proof._restore_bounds_census(family, text.replace(proof.DARK_PAIRING, "")) == [
        f"{family} restores a non-adaptive dark taken at another gain"]


@pytest.mark.parametrize("old, new", [
    ("\t\tif (!(m->tickdur > 0 && ", "\t\tif (!(1 && "),
    ("m->tickdur > 0 && m->minintcount > 0 && ", "m->tickdur > 0 && "),
    (" && m->min_int_time <= m->max_int_time", ""),
    ("\t\t   && m->cal_int_time > 0.0 && m->cal_int_time <= DBL_MAX)) {",
     "\t\t   && m->cal_int_time <= DBL_MAX)) {"),
    (" && m->cal_int_time <= DBL_MAX)) {", ")) {"),
    ("\t\tif (2.0 * NSEN_MAX * nmeas > (double)INT_MAX) {", "\t\tif (0) {"),
    ("\t\tif (m->calscantime > tmax)\n\t\t\ttmax = m->calscantime;\n", ""),
    ("\t\tif (m->ledpreheattime > tmax)\n\t\t\ttmax = m->ledpreheattime;\n", ""),
    ("m->cal_int_time < m->min_int_time ? m->cal_int_time : m->min_int_time",
     "m->min_int_time"),
])
def test_each_munki_timing_condition_is_needed(composition, tmp_path, old, new):
    """The sanitized probe must refuse the ColorMunki timing guard with any one
    condition weakened: a named refusal is accepted, or an accepted read overflows."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, _ = composition
    text = (source / MUNKI).read_text()
    block = proof._munki_timing_block(text)
    assert block is not None and block.count(old) == 1, old
    header = (source / "spectro/munki_imp.h").read_text()
    codes = "".join(proof._define(header, n) for n in ("MUNKI_OK", "MUNKI_DATA_RANGE"))
    codes += "".join(proof._define(text, n) for n in ("MAXSCANTIME", "NSEN_MAX"))
    folder = tmp_path / "probe"
    folder.mkdir()
    (folder / "block.inc").write_text(block.replace(old, new))
    (folder / "codes.inc").write_text(codes)
    (folder / "counts.inc").write_text("".join(
        proof._function(text, f) for f in ("munki_comp_nummeas", "munki_comp_ru_nummeas")))
    built = subprocess.run(["cc", "-std=gnu99", "-O1", "-fsanitize=undefined",
                            "-fno-sanitize-recover=all", f"-I{folder}", "-o", str(folder / "probe"),
                            str(ROOT / "tools/native_argyll/native_resync_munki_timing_probe.c"),
                            "-lm"], capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(folder / "probe")], capture_output=True, text=True, timeout=60)
    assert ran.returncode != 0 and ("FAIL" in ran.stdout or "runtime error" in ran.stderr)


@pytest.mark.parametrize("old, new, case", [
    ("\tif (strict && (!md || ((rf & 2) && !ed)))", "\tif (0)", "field_sign_only"),
    ("\tif (strict && (!md || ((rf & 2) && !ed)))", "\tif (strict && !md)",
     "field_bare_exponent"),
    ("errno == ERANGE && (dv == HUGE_VAL || dv == -HUGE_VAL)", "0", "field_real_overflow"),
])
def test_each_cgats_number_rule_is_needed(composition, tmp_path, old, new, case):
    """The composed CGATS reader with one number rule removed reads its case
    differently: a digitless token as a number, or an overflow as infinity."""
    if shutil.which(native.compiler_name()) is None:
        pytest.skip("C compiler unavailable")
    source, _ = composition
    text = (source / CGATS).read_text()
    assert text.count(old) == 1, old
    outcomes = _cgats_outcomes(source, text.replace(old, new), tmp_path / "mutant")
    assert CGATS_CASES[case][2] not in outcomes[case], (case, outcomes[case])


CGATS = "cgats/cgats.c"
# name: (NUMBER_OF_SETS, integer field value, expected composed outcome)
CGATS_CASES = {
    "in_range": ("1", "42", "read 1 42"),
    "int_max": ("1", "2147483647", "read 1 2147483647"),
    "int_min": ("1", "-2147483648", "read 1 -2147483648"),
    "just_above": ("1", "2147483648", "Field 1 value in set 0 is out of range"),
    "far_above": ("1", "99999999999", "Field 1 value in set 0 is out of range"),
    "far_below": ("1", "-99999999999", "Field 1 value in set 0 is out of range"),
    "set_count": ("99999999999", "42", "NUMBER_OF_SETS is invalid or out of range"),
    "whole_set_count": ("1.0", "42", "read 1 42"),
    "set_count_suffix": ("1garbage", "42", "NUMBER_OF_SETS is invalid or out of range"),
    # strtod underflows this to zero, which would switch off the set-count check
    "set_count_underflow": ("1e-999", "42", "NUMBER_OF_SETS is invalid or out of range"),
    "set_count_negative_underflow": ("-1e-999", "42", "NUMBER_OF_SETS is invalid or out of range"),
    # An exact zero, however written, is the unspecified count, as in pristine.
    "set_count_zero": ("0", "42", "read 1 42"),
    "set_count_negative_zero": ("-0", "42", "read 1 42"),
    "set_count_zero_exponent": ("0e-999", "42", "read 1 42"),
    # Rounds 11-12: a numeric token needs a digit, and one after any exponent;
    # a real that overflows is refused rather than read as infinity.
    "field_signed": ("1", "+3", "read 1 3"),
    "field_real": ("1", "2.5", "real 2.5"),
    "field_sign_only": ("1", "-", "string -"),
    "field_point_only": ("1", ".", "string ."),
    "field_bare_exponent": ("1", "1e", "string 1e"),
    "field_real_overflow": ("1", "1e999", "Field 1 value in set 0 is out of range"),
}


def _cgats_outcomes(source, text, folder):
    folder.mkdir()
    (folder / "cgats.c").write_text(text)
    build = subprocess.run(
        [native.compiler_name(), "-std=gnu99", "-O1", *sanitizers.flags(True),
         "-DSALONEINSTLIB", "-DNT" if sys.platform == "win32" else "-DUNIX",
         f"-I{source / 'cgats'}", f"-I{source / 'numlib'}",
         f"-I{source / 'h'}", f"-I{source}",
         str(ROOT / "tools/native_argyll/native_resync_cgats_probe.c"),
         str(folder / "cgats.c"), str(source / "cgats/pars.c"), "-lm",
         "-o", str(native.executable_path(folder, "probe"))],
        capture_output=True, text=True, timeout=120)
    assert build.returncode == 0, build.stderr
    (folder / "compile.log").write_text(build.stdout + build.stderr)
    outcomes = {}
    for name, (sets, value, _) in CGATS_CASES.items():
        case = folder / f"{name}.ti"
        case.write_text(f"PROBE\n\nNUMBER_OF_SETS {sets}\nNUMBER_OF_FIELDS 2\n"
                        "BEGIN_DATA_FORMAT\nSAMPLE_ID MYCOUNT\nEND_DATA_FORMAT\n"
                        f"BEGIN_DATA\n1 {value}\nEND_DATA\n")
        ran = subprocess.run([str(native.executable_path(folder, "probe")), str(case)],
                             capture_output=True,
                             text=True, timeout=10)
        (folder / f"{name}.log").write_text(ran.stdout + ran.stderr)
        assert ran.returncode == 0 and not ran.stderr, (name, ran.stderr)
        outcomes[name] = ran.stdout.strip()
    return outcomes


def test_cgats_releases_the_keyword_when_refusing_a_set_count(composition, tmp_path):
    """The rejected count's local keyword is not owned by the table destructor."""
    if shutil.which(native.compiler_name()) is None:
        pytest.skip("C compiler unavailable")
    source, _ = composition
    text = (source / CGATS).read_text()
    release = ("\t\t\t\t\t\tp->al->free(p->al, kw);"
               "\t/* CM: local keyword is not in the table */\n")
    assert text.count(release) == 1
    repaired = _cgats_outcomes(source, text, tmp_path / "repaired")
    assert "NUMBER_OF_SETS is invalid or out of range" in repaired["set_count"]
    with pytest.raises(AssertionError, match="CGATS allocator retained 1 block"):
        _cgats_outcomes(source, text.replace(release, ""), tmp_path / "unreleased")
    # Require the intended refusal and leak, rather than accepting an unrelated crash.
    log = (tmp_path / "unreleased/set_count.log").read_text()
    assert "NUMBER_OF_SETS is invalid or out of range" in log
    assert "CGATS allocator retained 1 block(s)" in log


def test_cgats_refuses_integers_it_cannot_store(composition, tmp_path):
    """The composed parser stores in-range integers exactly and refuses the rest;
    the pre-repair parser stores a different number than the file holds."""
    if shutil.which(native.compiler_name()) is None:
        pytest.skip("C compiler unavailable")
    source, before = composition
    repaired = _cgats_outcomes(source, (source / CGATS).read_text(), tmp_path / "repaired")
    for name, (_, _, expected) in CGATS_CASES.items():
        if expected.startswith(("read ", "real ", "string ")):
            assert expected == repaired[name], (name, repaired[name])
        else:
            location = ("in" if expected.startswith("Field ")
                        else r"at line [1-9][0-9]* of")
            case = tmp_path / "repaired" / f"{name}.ti"
            assert re.fullmatch(
                rf"refused -1 Error {location} file '{re.escape(str(case))}': "
                + re.escape(expected), repaired[name]), (name, repaired[name])
    baseline = _cgats_outcomes(source, before[CGATS], tmp_path / "baseline")
    # atoi overflow has no portable result: the old CRT may wrap or clamp.
    # It still stores an incorrect native int where the repaired reader refuses.
    for case in ("just_above", "far_above"):
        label, sets, value = baseline[case].split()
        assert (label, sets) == ("read", "1")
        assert -(2**31) <= int(value) <= 2**31 - 1
        assert int(value) != int(CGATS_CASES[case][1])
    assert baseline["set_count_suffix"] == "read 1 42"
    # The pre-port parser read these as numbers it made up.
    assert baseline["field_sign_only"] == "read 1 0"
    assert baseline["field_point_only"] == "real 0"
    assert baseline["field_bare_exponent"] == "real 1"
    assert baseline["field_real_overflow"] == "real inf"


CGATS_WRITE = r"""
#include <stdio.h>
#include "cgats.h"
int main(int argc, char **argv) {
    static const double values[] = {1.5, -2.25, 0.0, 1e300, 1e-300};
    static const char *names[] = {"-", "+", ".", "1e", "3", "abc", "x", "y"};
    double nan = 0.0 / 0.0, inf = 1.0 / 0.0;
    cgats *c = new_cgats();
    int i, result;
    (void)argc;
    c->add_other(c, "PROBE");
    c->add_table(c, tt_other, 0);
    c->add_field(c, 0, "VALUE", r_t);
    c->add_field(c, 0, "LABEL", nqcs_t);
    for (i = 0; i < 5; i++)
        c->add_set(c, 0, values[i], names[i]);
    c->add_set(c, 0, nan, names[5]);
    c->add_set(c, 0, inf, names[6]);
    c->add_set(c, 0, -inf, names[7]);
    result = c->write_name(c, argv[1]);
    c->del(c);
    return result != 0;
}
"""


def _cgats_written(source, text, folder):
    folder.mkdir()
    (folder / "cgats.c").write_text(text)
    (folder / "probe.c").write_text(CGATS_WRITE)
    build = subprocess.run(
        [native.compiler_name(), "-std=gnu99", "-O1", *sanitizers.UNDEFINED_FLAGS,
         "-DSALONEINSTLIB", "-DNT" if sys.platform == "win32" else "-DUNIX",
         f"-I{source / 'cgats'}", f"-I{source / 'numlib'}",
         f"-I{source / 'h'}", f"-I{source}", str(folder / "probe.c"), str(folder / "cgats.c"),
         str(source / "cgats/pars.c"), "-lm", "-o", str(native.executable_path(folder, "probe"))],
        capture_output=True, text=True, timeout=120)
    assert build.returncode == 0, build.stderr
    return subprocess.run([str(native.executable_path(folder, "probe")), str(folder / "out.ti")],
                          capture_output=True,
                          text=True, timeout=10), folder / "out.ti"


# The writer quotes any string an earlier reader would take for a
# number, so a file it writes reads the same in an earlier Argyll.
WRITTEN_LABELS = ['"-"', '"+"', '"."', '"1e"', '"3"', "abc", "x", "y"]


def _written_rows(out):
    data = out.read_text().split("BEGIN_DATA\n")[1].split("END_DATA")[0].split()
    return data[0::2], data[1::2]


def test_cgats_writes_an_infinite_real_as_text(composition, tmp_path):
    """The writer formats +-inf as it does NaN; the pre-port writer
    cast log10(inf) to int, which UBSan traps. Numeric-looking
    strings stay quoted."""
    if shutil.which(native.compiler_name()) is None:
        pytest.skip("C compiler unavailable")
    source, before = composition
    ran, out = _cgats_written(source, (source / CGATS).read_text(), tmp_path / "repaired")
    assert ran.returncode == 0 and not ran.stderr, ran.stderr
    values, labels = _written_rows(out)
    # NaN's printed sign is not part of the writer's numerical contract.
    assert math.isnan(float(values[5]))
    assert values[6:] == ["inf", "-inf"] and float(values[0]) == 1.5
    assert labels == WRITTEN_LABELS
    old, _ = _cgats_written(source, before[CGATS], tmp_path / "baseline")
    assert old.returncode != 0 and "runtime error" in old.stderr


def test_cgats_writer_finds_infinity_where_huge_val_is_finite(composition, tmp_path):
    """The writer tests a real against DBL_MAX. Where a C library
    defines HUGE_VAL as finite, a test against that constant misses a real
    infinity and casts log10(inf) to int, which UBSan traps."""
    if shutil.which(native.compiler_name()) is None:
        pytest.skip("C compiler unavailable")
    source, _ = composition
    text = (source / CGATS).read_text()
    header, guard = "#include <float.h>\n", "\tif (!(value >= -DBL_MAX && value <= DBL_MAX)) {"
    assert text.count(header) == 1 and text.count(guard) == 1
    text = text.replace(header, header + "#undef HUGE_VAL\n#define HUGE_VAL DBL_MAX\n")
    ran, out = _cgats_written(source, text, tmp_path / "repaired")
    assert ran.returncode == 0 and not ran.stderr, ran.stderr
    assert _written_rows(out)[0][6:] == ["inf", "-inf"]
    finite_huge_val_guard = "\tif (value != value || value == HUGE_VAL || value == -HUGE_VAL) {"
    old, _ = _cgats_written(source, text.replace(guard, finite_huge_val_guard),
                           tmp_path / "finite-huge-val")
    assert old.returncode != 0 and "runtime error" in old.stderr


def test_cgats_writer_keeps_the_earlier_quoting_grammar(composition, tmp_path):
    """With the writer's guess_type made strict,
    '-', '+', '.' and '1e' are written bare, which an earlier reader takes
    for numbers."""
    if shutil.which(native.compiler_name()) is None:
        pytest.skip("C compiler unavailable")
    source, _ = composition
    text = (source / CGATS).read_text()
    old = "guess_type((char *)t->fdata[set][field], 0)"
    assert text.count(old) == 1
    ran, out = _cgats_written(source, text.replace(old, old[:-2] + "1)"), tmp_path / "mutant")
    assert ran.returncode == 0, ran.stderr
    _, labels = _written_rows(out)
    assert labels[:4] == ["-", "+", ".", "1e"] and labels != WRITTEN_LABELS


CCMX = ("xicc/ccmx.c", "xicc/xspect.c", "xicc/xspect.h")
# DISPLAY_TYPE_BASE_ID text: composed outcome
CCMX_CASES = {
    "0": "read 0",
    "2": "read 2",
    '"2"': "read 2",
    "2147483647": "read 2147483647",
    "2147483648": "refused read_ccmx: Input file has out of range DISPLAY_TYPE_BASE_ID",
    "99999999999": "refused read_ccmx: Input file has out of range DISPLAY_TYPE_BASE_ID",
    "2.5": "refused read_ccmx: Input file has out of range DISPLAY_TYPE_BASE_ID",
    "4garbage": "refused read_ccmx: Input file has out of range DISPLAY_TYPE_BASE_ID",
    "nan": "refused read_ccmx: Input file has out of range DISPLAY_TYPE_BASE_ID",
    "-1": "refused read_ccmx: Input file has out of range DISPLAY_TYPE_BASE_ID",
    "1e-999": "refused read_ccmx: Input file has out of range DISPLAY_TYPE_BASE_ID",
}


def _ccmx_outcomes(source, texts, folder):
    folder.mkdir()
    for name in CCMX:
        (folder / Path(name).name).write_text(texts[name])
    (folder / "sa_config.h").write_text((source / "h/aconfig.h").read_text())
    includes = [f"-I{folder}", f"-I{source}"] + [
        f"-I{source / d}" for d in ("spectro", "numlib", "xicc", "cgats", "h", "rspl")]
    support = [source / p for p in ("numlib/numsup.c", "spectro/sa_conv.c", "cgats/cgats.c",
                                    "cgats/pars.c", "spectro/disptechs.c")]
    system = (["-framework", "CoreFoundation"] if sys.platform == "darwin" else
              ["-lwinmm"] if sys.platform == "win32" else [])
    build = subprocess.run(
        [native.compiler_name(), "-std=gnu99", "-O1",
         "-fsanitize=undefined", "-fno-sanitize-recover=all",
         "-DSALONEINSTLIB",
         *(["-DNT", "-DEN_LIBUSB0"] if sys.platform == "win32" else ["-DUNIX", "-pthread"]),
         *includes,
         str(ROOT / "tools/native_argyll/native_resync_ccmx_probe.c"),
         str(folder / "ccmx.c"), str(folder / "xspect.c"), *map(str, support), *system,
         "-lm", "-o", str(native.executable_path(folder, "probe"))],
        capture_output=True, text=True, timeout=240)
    assert build.returncode == 0, build.stderr
    outcomes = {}
    for index, value in enumerate(CCMX_CASES):
        case = folder / f"case{index}.ccmx"
        case.write_text('CCMX\n\nDESCRIPTOR "probe"\nINSTRUMENT "probe"\nDISPLAY "probe"\n'
                        f'DISPLAY_TYPE_BASE_ID {value}\nCOLOR_REP "XYZ"\nNUMBER_OF_FIELDS 3\n'
                        "BEGIN_DATA_FORMAT\nXYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\n"
                        "NUMBER_OF_SETS 3\nBEGIN_DATA\n1 0 0\n0 1 0\n0 0 1\nEND_DATA\n")
        ran = subprocess.run([str(native.executable_path(folder, "probe")), str(case)],
                             capture_output=True, text=True, timeout=10)
        assert ran.returncode == 0 and not ran.stderr, (value, ran.stderr)
        outcomes[value] = ran.stdout.strip()
    return outcomes


def test_ccmx_reads_the_display_base_id_whole_or_refuses_it(composition, tmp_path):
    """Composed read_ccmx stores the whole integral value or refuses; the
    pre-repair reader keeps a prefix or truncates a fraction."""
    if shutil.which(native.compiler_name()) is None:
        pytest.skip("C compiler unavailable")
    source, before = composition
    repaired = _ccmx_outcomes(source, {n: (source / n).read_text() for n in CCMX},
                              tmp_path / "repaired")
    for value, expected in CCMX_CASES.items():
        assert expected == repaired[value], (value, repaired[value])
    baseline = _ccmx_outcomes(source, {n: before[n] for n in CCMX}, tmp_path / "baseline")
    assert baseline["4garbage"] == "read 4" and baseline["2.5"] == "read 2"
    assert baseline["-1"] == "read -1"


# Decode signed device words without shifting a negative or
# overflowing int. Each unit runs the pre-repair functions (built with -fwrapv,
# the two's-complement result they relied on) and the composed ones under
# UBSan over the same inputs: every 16-bit pattern in the first two bytes (the
# later bytes cycle through edge values), then 334,464 pseudo-random buffers.
# The results must agree, and the pre-repair functions must trap under UBSan,
# which is the defect.
DECODER_PRELUDE = r"""
#include <limits.h>
#include <stdio.h>
#include <string.h>
typedef unsigned char ORD8; typedef unsigned short ORD16; typedef unsigned int ORD32;
typedef unsigned long long ORD64; typedef signed char INR8; typedef short INR16;
typedef int INR32; typedef long long INR64;
typedef int inst_code;
#define inst_ok 0
typedef struct probe_dev huey;
typedef struct probe_dev i1disp;
static int probe_bytes[4], probe_base;
static inst_code huey_rdreg_byte(huey *p, int *outp, int addr) {
    (void)p; *outp = probe_bytes[addr - probe_base]; return inst_ok;
}
static inst_code i1disp_rdreg_byte(i1disp *p, int *outp, int addr) {
    (void)p; *outp = probe_bytes[addr - probe_base]; return inst_ok;
}
"""
DECODER_MAIN = r"""
static unsigned long long hash = 1469598103934665603ULL, rng = 88172645463325252ULL;
static void mix(unsigned long long v) { hash ^= v; hash *= 1099511628211ULL; }
static unsigned char next_byte(void) {
    rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (unsigned char)(rng >> 24);
}
int main(void) {
    static const unsigned char edge[4] = {0x00, 0x7f, 0x80, 0xff};
    unsigned char b[64];
    long i;
    int j;
    for (i = 0; i < 400000; i++) {
        for (j = 0; j < 64; j++)
            b[j] = i >= 65536 ? next_byte() : j == 0 ? (unsigned char)(i >> 8)
                 : j == 1 ? (unsigned char)i : edge[(i + j) & 3];
        BODY
    }
    printf("%016llx\n", hash);
    return 0;
}
"""
WORD = ("{ int out = 0, k; for (k = 0; k < 4; k++) probe_bytes[k] = b[k]; probe_base = 7; "
        "%s(NULL, &out, 7); mix((unsigned)out); }")
DECODER_UNITS = {
    "numlib/numsup.c": (
        ["read_ORD16_be", "read_ORD16_le", "read_ORD32_be", "read_ORD32_le", "read_ORD64_be",
         "read_ORD64_le", "read_INR16_be", "read_INR16_le", "read_INR32_be", "read_INR32_le",
         "read_INR64_be", "read_INR64_le"],
        "".join(f"mix((ORD64)read_INR{n}_{e}(b));" for n in (16, 32, 64) for e in ("be", "le"))),
    "spectro/colorhug.c": (["buf2int_le"], "mix((unsigned)buf2int_le(b));"),
    "spectro/huey.c": (["buf2int", "huey_rdreg_word"],
                       "mix((unsigned)buf2int(b));" + WORD % "huey_rdreg_word"),
    "spectro/i1disp.c": (["buf2int", "i1disp_rdreg_word"],
                         "mix((unsigned)buf2int(b));" + WORD % "i1disp_rdreg_word"),
    "spectro/spyd2.c": (["buf2int"], "mix((unsigned)buf2int(b));"),
    "spectro/i1pro_imp.c": (["buf2int", "buf2short"],
                            "mix((unsigned)buf2int(b)); mix((unsigned)buf2short(b));"),
    "spectro/munki_imp.c": (["buf2int", "buf2short"],
                            "mix((unsigned)buf2int(b)); mix((unsigned)buf2short(b));"),
    "spectro/i1d3.c": (["buf2int", "create_unlock_response"],
                       "mix((unsigned)buf2int(b)); { unsigned int k[2]; unsigned char r[64];"
                       " memcpy(k, b + 48, sizeof(k)); create_unlock_response(k, b, r);"
                       " for (j = 0; j < 64; j++) mix(r[j]);"
                       " escape_keys(b + 50, k); mix(k[0]); mix(k[1]); }"),
}


def _escape_keys(text):
    """The I1D3_ESCAPE key assembly from get_escape_code, as its own function."""
    function = _function(text, "get_escape_code")
    start = function.index("\t\t\tk0 = ")
    end = function.index(";\n", function.index("\t\t\tk1 = ", start)) + 2
    return ("static void escape_keys(unsigned char *k, unsigned int *out) {\n"
            "\tunsigned int k0, k1;\n" + function[start:end].replace(",\n", ";\n", 1)
            + "\tout[0] = k0; out[1] = k1;\n}\n")


def _decoder_run(folder, path, text, flags):
    names, body = DECODER_UNITS[path]
    functions = "".join(_function(text, n) + "\n" for n in names)
    if path == "spectro/i1d3.c":
        functions += _escape_keys(text)
    folder.mkdir()
    (folder / "probe.c").write_text(DECODER_PRELUDE + functions
                                    + DECODER_MAIN.replace("BODY", body))
    built = subprocess.run(["cc", "-std=gnu99", "-O1", *flags, str(folder / "probe.c"),
                            "-o", str(folder / "probe")],
                           capture_output=True, text=True, timeout=120)
    assert built.returncode == 0, built.stderr
    return subprocess.run([str(folder / "probe")], capture_output=True, text=True, timeout=60)


UBSAN = ("-fsanitize=undefined", "-fno-sanitize-recover=all")


@pytest.mark.parametrize("path", sorted(DECODER_UNITS))
def test_signed_decoders_keep_their_values_without_undefined_shifts(composition, tmp_path, path):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, before = composition
    old = _decoder_run(tmp_path / "old", path, before[path], ("-fwrapv",))
    new = _decoder_run(tmp_path / "new", path, (source / path).read_text(), UBSAN)
    assert old.returncode == 0 and new.returncode == 0, new.stderr
    assert new.stdout == old.stdout and not new.stderr
    trapped = _decoder_run(tmp_path / "old-ubsan", path, before[path], UBSAN)
    assert trapped.returncode != 0 and "runtime error" in trapped.stderr


MD5_PROBE = r"""
#include <stdio.h>
#include <string.h>
#include "numsup.h"
#include "sa_conv.h"
static void digest(const unsigned char *data, unsigned int len) {
    unsigned char sum[16];
    int i;
    sa_MD5 *m = new_sa_MD5();
    m->add(m, (ORD8 *)data, len);
    m->get(m, sum);
    for (i = 0; i < 16; i++) printf("%02x", sum[i]);
    printf("\n");
    m->del(m);
}
int main(void) {
    static const char *rfc[] = {"", "a", "abc", "message digest", "abcdefghijklmnopqrstuvwxyz",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
        "12345678901234567890123456789012345678901234567890123456789012345678901234567890"};
    unsigned char buf[300];
    unsigned long long rng = 88172645463325252ULL;
    int i, j;
    for (i = 0; i < 7; i++) digest((const unsigned char *)rfc[i], strlen(rfc[i]));
    for (i = 0; i < 2000; i++) {
        for (j = 0; j < 300; j++) {
            rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17;
            buf[j] = (unsigned char)(rng >> 24);
        }
        digest(buf, (unsigned int)(i % 300));
    }
    return 0;
}
"""
MD5_RFC1321 = ["d41d8cd98f00b204e9800998ecf8427e", "0cc175b9c0f1b6a831c399e269772661",
               "900150983cd24fb0d6963f7d28e17f72", "f96b697d7cb7938d525a2f31aaf161d0",
               "c3fcd3d76192e4007dfb496cca67e13b", "d174ab98d277d9f5a5611c2c9f419d9f",
               "57edf4a22be3c955ac49da2e2107b67a"]


def _md5_run(source, folder, sa_conv, flags):
    folder.mkdir()
    (folder / "sa_conv.c").write_text(sa_conv)
    (folder / "probe.c").write_text(MD5_PROBE)
    (folder / "sa_config.h").write_text((source / "h/aconfig.h").read_text())
    system = (["-framework", "CoreFoundation"] if sys.platform == "darwin" else
              ["-lwinmm"] if sys.platform == "win32" else [])
    built = subprocess.run(
        [native.compiler_name(), "-std=gnu99", "-O1", *flags, "-DSALONEINSTLIB",
         *(["-DNT", "-DEN_LIBUSB0"] if sys.platform == "win32" else ["-DUNIX", "-pthread"]),
         f"-I{folder}",
         *(f"-I{source / d}" for d in ("spectro", "numlib", "h")),
         str(folder / "probe.c"), str(folder / "sa_conv.c"), str(source / "numlib/numsup.c"),
         *system, "-lm", "-o", str(native.executable_path(folder, "probe"))],
        capture_output=True, text=True, timeout=240)
    assert built.returncode == 0, built.stderr
    return subprocess.run([str(native.executable_path(folder, "probe"))], capture_output=True,
                          text=True, timeout=60)


def test_sa_conv_md5_matches_rfc1321_without_undefined_shifts(composition, tmp_path):
    """The EX1 driver's checksum (ex1.c new_icmMD5 -> sa_conv.c) keeps its digests."""
    if shutil.which(native.compiler_name()) is None:
        pytest.skip("C compiler unavailable")
    source, before = composition
    path = "spectro/sa_conv.c"
    new = _md5_run(source, tmp_path / "new", (source / path).read_text(), UBSAN)
    assert new.returncode == 0 and not new.stderr, new.stderr
    assert new.stdout.split()[:7] == MD5_RFC1321
    old = _md5_run(source, tmp_path / "old", before[path], ("-fwrapv",))
    assert old.stdout == new.stdout
    trapped = _md5_run(source, tmp_path / "old-ubsan", before[path], UBSAN)
    assert trapped.returncode != 0 and "runtime error" in trapped.stderr


def test_polarized_oracle_rejects_nan_after_the_calculation(
        composition, tmp_path, monkeypatch):
    """A NaN returned after the compiled block fails every in-range case by name.

    Both texts run this one template and the baseline is validated first, so its
    exact failure set is what rejects the NaN: the four in-range cases join the
    three pristine out-of-range failures. A NaN-blind comparison would keep that
    set exact and the repaired run clean, and the proof would pass.
    """
    if shutil.which(native.compiler_name()) is None:
        pytest.skip("C compiler unavailable")
    source, before = composition
    template = tmp_path / "templates"
    template.mkdir()
    for original in proof.HERE.glob("native_resync_*probe.c"):
        shutil.copyfile(original, template / original.name)
    probe = template / "native_resync_polarized_probe.c"
    text = probe.read_text()
    assert text.count("\treturn inttime;") == 1
    probe.write_text(text.replace("\treturn inttime;", "\treturn NAN;"))
    monkeypatch.setattr(proof, "HERE", template)
    with pytest.raises(AssertionError, match="baseline failures differs at polarized") as caught:
        proof.prove(source, before, tmp_path / "nan-oracle", groups=("polarized",))
    for case in ("nominal", "bright", "dim_in_range", "negative_white"):
        assert f"'{case}: inttime " in str(caught.value)


@pytest.mark.parametrize("probe,output", [
    ("curve-i1pro", "FAIL shifted maxval free\nFAIL partial allocation retained\n"
                    "FAIL shifted maxval free\nFAIL partial allocation retained\n"
                    "curve: 4 failures\n"),
    ("matrix-i1pro3", "FAIL partial matrix persisted\nFAIL same-object retry incomplete\n"
                       "FAIL partial matrix persisted\nFAIL same-object retry incomplete\n"
                       "matrix: 4 failures\n"),
    ("debug", "FAIL quiet level 0 touches diagnostics\nFAIL quiet level 2 touches diagnostics\n"
              "debug: 2 failures\n"),
])
def test_resync_baseline_requires_exact_semantic_failures(probe, output):
    proof._validate_failures(probe, output, baseline=True)
    first = next(line for line in output.splitlines() if line.startswith("FAIL "))
    unrelated = output.replace(first, "FAIL unrelated baseline fault", 1)
    with pytest.raises(AssertionError, match="baseline failures"):
        proof._validate_failures(probe, unrelated, baseline=True)
    with pytest.raises(AssertionError, match="baseline failures"):
        proof._validate_failures(probe, output.replace(first + "\n", "", 1), baseline=True)
    with pytest.raises(AssertionError, match="baseline failures"):
        proof._validate_failures(probe, output + first + "\n", baseline=True)
    with pytest.raises(AssertionError, match="count disagrees"):
        proof._validate_failures(probe, output.replace("4 failures", "5 failures")
                                  .replace("2 failures", "3 failures"), baseline=True)


def test_resync_clean_repaired_output_does_not_accept_failed_checks():
    proof._validate_failures("curve-i1pro", "curve: 0 failures\n", baseline=False)
    with pytest.raises(AssertionError, match="behavior"):
        proof._validate_failures("curve-i1pro", "FAIL unrelated fault\ncurve: 1 failures\n",
                                baseline=False)
    with pytest.raises(AssertionError, match="count disagrees"):
        proof._validate_failures("curve-i1pro", "curve: 1 failures\n", baseline=False)


@pytest.mark.parametrize("probe,case", [
    ("average-i1pro", "i1pro nan_cell"),
    ("white-factors", "nan_band"),
    ("polarized", "tiny_white"),
    ("dtp41", "sample activation fails"),
])
def test_baseline_case_prefix_cannot_hide_an_unrelated_failed_property(probe, case):
    if probe == "dtp41":
        acquisition = ('returned 0x0 (want 0x70000), sent "x" (want "y"), '
                       'mode 0x0/0x0 (want 0x1/0x1), commands 1')
        calibration = ('returned 0x0 (want 0x120000), calibration 0x20 (want 0x800), '
                       'condition 0x1 (want 0x2), needs calibration 1 (want 1), commands 0')
        names = [name + ": " + (calibration if name.startswith(
            ("calibration ", "transmission white ")) else acquisition)
                 for name in proof.BASELINE_FAILURES[probe].elements()]
    else:
        names = list(proof.BASELINE_FAILURES[probe].elements())
    output = "".join("FAIL " + name + "\n" for name in names)
    proof._validate_failures(probe, output, baseline=True)
    original = next(name for name in names if name.startswith(case + ": "))
    unrelated = output.replace(
        "FAIL " + original, "FAIL " + case + ": unrelated allocation fault", 1)
    with pytest.raises(AssertionError, match="baseline failures|unrelated DTP41"):
        proof._validate_failures(probe, unrelated, baseline=True)
