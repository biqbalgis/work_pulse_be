from datetime import date


def _year_prefix() -> str:
    """Last two digits of the current year, e.g. '26' for 2026."""
    return str(date.today().year)[2:]


def get_next_job_code(workspace) -> str:
    """
    Return the next available job code for the given workspace and current year,
    WITHOUT saving it.

    Format: {2-digit-year}{4-digit-sequence}
      e.g.  260001, 260002, ..., 260013, ... 270001, ...

    Sequence is per-workspace — two different workspaces each have their own
    independent 260001, 260002, ... counters.

    Only counts codes that match the auto-generated pattern (exactly 6 digits
    starting with the current year prefix), so manually entered codes like
    '26GC001' don't corrupt the sequence.
    """
    from .models import Project

    prefix = _year_prefix()

    last_code = (
        Project.objects
        .filter(
            workspace=workspace,
            job_code__startswith=prefix,
            job_code__regex=r'^\d{6}$',
            is_deleted=False,
        )
        .order_by('-job_code')
        .values_list('job_code', flat=True)
        .first()
    )

    if last_code:
        try:
            seq = int(last_code[2:]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1

    return f"{prefix}{seq:04d}"
