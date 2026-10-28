#ifndef PR655_H
#define PR655_H
/*
 * Argyll Color Management System
 * Photo Research PR-655 and PR-670 instrument driver.
 * Copyright 2026 Fernando Voltolini de Azambuja.
 * SPDX-License-Identifier: GPL-2.0-or-later
 * This material is licensed under the GNU GENERAL PUBLIC LICENSE Version 2
 * or later; see License2.txt. Protocol sources and limitations: pr655.txt.
 */
#ifndef SALONEINSTLIB
#include "aconfig.h"
#else
#include "sa_config.h"
#endif
#include "numsup.h"
#include "cgats.h"
#include "xspect.h"
#include "conv.h"
#include "insttypes.h"
#include "pr655_imp.h"

#define PR655_GEOMETRY_ERROR    0xff20
#define PR655_UNIT_MISMATCH     0xff21
#define PR655_CAPTURE_MISMATCH  0xff22
#define PR655_BUDGET_ERROR      0xff23
#define PR655_ALREADY_OPEN      0xff24

#define PR655_UNIT_NOT_CHECKED   0
#define PR655_UNIT_CONSISTENT    1
#define PR655_UNIT_INCONCLUSIVE  2
#define PR655_UNIT_REFUSED       3
#define PR655_CHECK_POWER        1
#define PR655_CHECK_Y            2

typedef struct {
	int verdict, discriminating, computed;
	double power_w, xyz[3], power_ratio, y_ratio, xyz_scale;
} pr655_unit_check;

typedef struct {
	pr655_capture native;
	pr655_setup before, after;
	pr655_unit_check unit;
	double budget_s;
	int valid, setup_before_valid, setup_after_valid;
	char problem[256];
} pr655_capture_result;

typedef struct {
	INST_OBJ_BASE
	amutex lock;
	pr655imp engine;
	inst_opt_type trigger;
	inst_mode mode;
	int averages;
	double refresh_hz;
	pr655_capture_result capture;
	char error_message[384];
} pr655;

#ifdef __cplusplus
extern "C" {
#endif
/* Success transfers ownership of icom. Construction performs no I/O. */
pr655 *new_pr655(icoms *icom, instType dtype);
#ifdef __cplusplus
}
#endif
#endif
