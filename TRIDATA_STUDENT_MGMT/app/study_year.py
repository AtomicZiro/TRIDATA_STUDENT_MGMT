"""Map semester numbers to program year (Year 1, Year 2, …)."""

SEMS_PER_YEAR = 2


def program_year_from_semester(semester: int, semesters_per_year: int = SEMS_PER_YEAR) -> int:
    if semester < 1:
        return 1
    return (semester - 1) // semesters_per_year + 1


def semester_bounds_for_program_year(
    study_year: int,
    semesters_per_year: int = SEMS_PER_YEAR,
) -> tuple[int, int]:
    """Inclusive semester range for the given program year (e.g. Year 1 → sem 1–2 when 2 sems/year)."""
    lo = (study_year - 1) * semesters_per_year + 1
    hi = study_year * semesters_per_year
    return lo, hi
