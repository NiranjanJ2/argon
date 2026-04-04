"""Whitney High School 2025-2026 bell schedules.

Each schedule is a list of (label, start_hhmm, end_hhmm) tuples.
Times are 24-hour integers, e.g. 8:30 AM = 830, 1:30 PM = 1330.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Schedule data
# ---------------------------------------------------------------------------

# Each entry: (label, start HHMM, end HHMM)
ScheduleEntry = tuple[str, int, int]

SCHEDULES: dict[str, list[ScheduleEntry]] = {
    "regular": [
        ("Period 0",  730,  825),
        ("Period 1",  830,  928),
        ("Period 2",  933, 1030),
        ("Snack",    1030, 1045),
        ("Period 3", 1050, 1147),
        ("Period 4", 1152, 1249),
        ("Lunch",    1249, 1321),
        ("Period 5", 1326, 1423),
        ("HR",       1423, 1434),
        ("Period 6", 1439, 1536),
    ],
    "early_release": [
        ("Period 0",  730,  825),
        ("Period 1",  830,  921),
        ("Period 2",  926, 1016),
        ("Snack",    1016, 1031),
        ("Period 3", 1036, 1126),
        ("Period 4", 1131, 1221),
        ("Lunch",    1221, 1251),
        ("Period 5", 1256, 1346),
        ("Period 6", 1351, 1441),
        ("Meeting",  1450, 1550),
    ],
    "advisement": [
        ("Period 0",  730,  825),
        ("Period 1",  830,  923),
        ("Period 2",  928, 1020),
        ("Snack",    1020, 1035),
        ("Period 3", 1040, 1132),
        ("Period 4", 1137, 1229),
        ("Lunch",    1229, 1301),
        ("Period 5", 1306, 1358),
        ("Adv/HR",   1358, 1439),
        ("Period 6", 1444, 1536),
    ],
    "activity": [
        ("Period 0",  730,  825),
        ("Period 1",  830,  924),
        ("Period 2",  929, 1022),
        ("Snack",    1022, 1037),
        ("Period 3", 1042, 1135),
        ("Period 4", 1140, 1233),
        ("Activity", 1233, 1308),
        ("Lunch",    1308, 1340),
        ("Period 5", 1345, 1438),
        ("Period 6", 1443, 1536),
    ],
    "minimum_day": [
        ("Period 0",  750,  825),
        ("Period 1",  830,  907),
        ("Period 2",  912,  948),
        ("Period 3",  953, 1029),
        ("Snack",    1029, 1049),
        ("Period 4", 1054, 1130),
        ("Period 5", 1135, 1211),
        ("Period 6", 1216, 1252),
    ],
    "special_events": [
        ("Period 0",  730,  825),
        ("Period 1",  830,  921),
        ("Period 2",  926, 1016),
        ("Snack",    1016, 1031),
        ("Period 3", 1036, 1126),
        ("Period 4", 1131, 1221),
        ("Event",    1221, 1314),
        ("Lunch",    1314, 1346),
        ("Period 5", 1351, 1441),
        ("Period 6", 1446, 1536),
    ],
    "comp_1st_qtr": [
        ("Period 0",  730,  825),
        ("1st Comp",  830, 1033),
        ("Snack",    1033, 1048),
        ("2nd Comp", 1053, 1256),
        ("Lunch",    1256, 1328),
        ("3rd Comp", 1333, 1536),
    ],
    "comp_semester": [
        ("Period 0",  730,  825),
        ("1st Comp",  830, 1030),
        ("Snack",    1030, 1055),
        ("2nd Comp", 1100, 1300),
    ],
    "first_day": [
        ("Period 0",   730,  825),
        ("Rally",      830,  900),
        ("Schedule",   900,  915),
        ("Period 1",   920, 1006),
        ("Period 2",  1011, 1057),
        ("Snack",     1057, 1117),
        ("Period 3",  1122, 1208),
        ("Period 4",  1213, 1259),
        ("Lunch",     1259, 1339),
        ("Period 5",  1344, 1430),
        ("Period 6",  1435, 1521),
    ],
}

# Default schedule by weekday (0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri)
DEFAULT_BY_WEEKDAY: dict[int, str] = {
    0: "regular",        # Monday
    1: "early_release",  # Tuesday
    2: "advisement",     # Wednesday
    3: "regular",        # Thursday
    4: "regular",        # Friday (sometimes activity — needs manual override)
}

SCHEDULE_DISPLAY_NAMES: dict[str, str] = {
    "regular":        "Regular (M/Th/F)",
    "early_release":  "Early Release (Tuesday)",
    "advisement":     "Advisement (Wednesday)",
    "activity":       "Activity Friday",
    "minimum_day":    "Minimum Day",
    "special_events": "Special Events",
    "comp_1st_qtr":   "1st Quarter Comps",
    "comp_semester":  "Semester / 3rd Qtr Comps",
    "first_day":      "First Day of School",
}
