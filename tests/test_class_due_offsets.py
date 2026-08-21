"""Classes that collect work before the date Classroom shows.

Machado takes Math Analysis the lesson before the assignment's date, so an item
Classroom calls Thursday is really due Wednesday. This was recorded as a
standing memory fact, which told the model but left the board — the thing he
actually looks at — still showing it under Today on the day it was late.
"""

from argon.commitments import _from_assignment


def _assignment(course: str, due: str = "2026-08-20T23:59:00-07:00") -> dict:
    return {"title": "HW 5", "course_name": course, "due": due, "classroom_key": "k"}


class TestMathIsDueEarlier:
    def test_the_work_by_date_moves_a_day_earlier(self):
        c = _from_assignment(_assignment("Math Analysis/Calc A H-Machado(26-27)"), None)

        assert c.work_by == "2026-08-19"
        # The board's derived date follows work_by, which is what buckets it.
        assert c.due == "2026-08-19"

    def test_the_official_deadline_is_left_alone(self):
        c = _from_assignment(_assignment("Math Analysis/Calc A H-Machado(26-27)"), None)

        # Classroom owns the deadline; only his working date shifts.
        assert c.official_due == "2026-08-20T23:59:00-07:00"

    def test_other_classes_are_untouched(self):
        c = _from_assignment(_assignment("APUSH PM"), None)

        assert c.work_by is None
        assert c.due == "2026-08-20T23:59:00-07:00"

    def test_a_date_he_set_himself_wins(self):
        # The class rule only fills a gap. If he has planned a date, that is
        # the answer, whatever the class normally does.
        c = _from_assignment(
            _assignment("Math Analysis/Calc A H-Machado(26-27)"),
            {"work_by": "2026-08-17"},
        )

        assert c.work_by == "2026-08-17"

    def test_an_assignment_with_no_deadline_gains_none(self):
        c = _from_assignment(_assignment("Math Analysis/Calc A", due=None), None)

        assert c.work_by is None
