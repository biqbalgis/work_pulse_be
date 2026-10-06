"""
Custom Report builder — Clockify-style summary reports exported as Excel.

The caller picks filters (projects / employees / date range), a time interval
(daily / weekly / biweekly / monthly), which metrics to show (total / regular /
overtime) and how rows are grouped (up to two of project / employee / task).
Nothing here is persisted — the result is a one-off .xlsx, never a LEMReport.

Regular vs Overtime follows each workspace's overtime policy
(Workspace.overtime_policy), the same rules approvals/payroll use
(approvals.utils.calculate_rt_ot_and_cost):

  "envision" — an Alberta statutory holiday is 100% OT (and doesn't count toward
               the weekly Regular hours). On every other day the first 8h is
               Regular and anything beyond is OT, and Regular is capped at 44h
               per Sun-Sat week: once a week's Regular hours reach 44, the
               remaining hours that week are OT.
  "standard" — per day and per project, Regular up to project.default_rt_hours
               (8h when unset), the rest is OT.
  none       — every hour is Regular.

For Envision that split is a property of an employee's TOTAL hours that
day/week across every project, so it is computed over full Sun-Sat weeks of ALL
the employee's entries first, walking them in the order they were worked
(start time, Mountain-time days) — like approvals' Envision calculation, but
entries that start at the same moment share the split in proportion to hours —
and each entry's Regular/OT is then added to its report cell if it passes the
project filter.
"""

import io
from collections import defaultdict
from datetime import timedelta
from itertools import groupby

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from approvals.utils import ENVISION_DAILY_RT_CAP, ENVISION_WEEKLY_RT_CAP
from core.utils.envision_time import alberta_stat_holidays, utc_to_envision_local
from workspaces.models import WorkspaceMember
from .envision_timesheet_utils import _week_sunday

ENVISION_DAILY_RT = float(ENVISION_DAILY_RT_CAP)    # 8h Regular per day
ENVISION_WEEKLY_RT = float(ENVISION_WEEKLY_RT_CAP)  # 44h Regular per Sun-Sat week
STANDARD_DEFAULT_DAILY_RT = 8.0

METRICS_ORDER = ("total", "regular", "overtime")
METRIC_LABELS = {"total": "Total", "regular": "Regular", "overtime": "Overtime"}
METRIC_INDEX = {"total": 0, "regular": 1, "overtime": 2}
GROUP_DIMS = ("project", "user", "task", "group")
GROUP_LABELS = {"project": "Project", "user": "Employee", "task": "Task", "group": "User Group"}
INTERVALS = ("daily", "weekly", "biweekly", "monthly")
CHART_TYPES = ("bar", "line", "pie")

MAX_RANGE_DAYS = 366
MAX_DAILY_RANGE_DAYS = 92
MAX_CHART_CATEGORIES = 30


# ── Time buckets ──────────────────────────────────────────────────────────────

def week_range_for(date_from, date_to):
    """Full Sun-Sat span covering [date_from, date_to] — needed so the weekly
    44h Regular cap is evaluated from the true start of the first week."""
    return _week_sunday(date_from), _week_sunday(date_to) + timedelta(days=6)


def build_buckets(date_from, date_to, interval):
    """Return (buckets, date_to_bucket). Each bucket is {start, end, label},
    clipped to [date_from, date_to]. Weekly/biweekly periods are anchored to
    the Sunday on or before date_from, then step 7 / 14 days. Monthly periods
    are calendar months (the first/last may be partial when the range doesn't
    start on the 1st / end on the month's last day)."""
    multi_year = date_from.year != date_to.year
    day_fmt = "%a %b %d, %Y" if multi_year else "%a %b %d"
    range_fmt = "%b %d, %Y" if multi_year else "%b %d"

    buckets = []
    if interval == "daily":
        d = date_from
        while d <= date_to:
            buckets.append({"start": d, "end": d, "label": d.strftime(day_fmt)})
            d += timedelta(days=1)
    elif interval == "monthly":
        start = date_from
        while start <= date_to:
            next_month = (start.replace(day=1) + timedelta(days=32)).replace(day=1)
            month_end = next_month - timedelta(days=1)
            b_end = min(month_end, date_to)
            if start.day == 1 and b_end == month_end:
                label = start.strftime("%b %Y")  # a whole calendar month
            elif start == b_end:
                label = start.strftime(range_fmt)
            else:
                label = "{} - {}".format(start.strftime(range_fmt), b_end.strftime(range_fmt))
            buckets.append({"start": start, "end": b_end, "label": label})
            start = next_month
    else:
        step = 7 if interval == "weekly" else 14
        start = _week_sunday(date_from)
        while start <= date_to:
            b_start = max(start, date_from)
            b_end = min(start + timedelta(days=step - 1), date_to)
            label = (
                b_start.strftime(range_fmt)
                if b_start == b_end
                else "{} - {}".format(b_start.strftime(range_fmt), b_end.strftime(range_fmt))
            )
            buckets.append({"start": b_start, "end": b_end, "label": label})
            start += timedelta(days=step)

    date_to_bucket = {}
    for idx, bucket in enumerate(buckets):
        d = bucket["start"]
        while d <= bucket["end"]:
            date_to_bucket[d] = idx
            d += timedelta(days=1)
    return buckets, date_to_bucket


# ── Aggregation ───────────────────────────────────────────────────────────────

def _dim_key(entry, dim, group_lookup=None):
    """(display label, stable id) for one grouping dimension of an entry.
    group_lookup: {(user_id, workspace_id): group value} for the "group" dimension."""
    if dim == "group":
        value = (group_lookup or {}).get((entry.user_id, entry.workspace_id))
        if value:
            return (dict(WorkspaceMember.GROUP_CHOICES).get(value, value), value)
        return ("(No Group)", "")
    if dim == "project":
        if entry.project_id:
            return (entry.project.name, str(entry.project_id))
        return ("(No Project)", "")
    if dim == "user":
        return (entry.user.get_full_name() or entry.user.email, str(entry.user_id))
    if entry.task_id:
        return (entry.task.name, str(entry.task_id))
    return ("(No Task)", "")


def aggregate_hours(entries, date_from, date_to, date_to_bucket, group_by, include_entry,
                    group_lookup=None, policy_by_workspace=None):
    """
    entries             — every TimeEntry (all projects) for the relevant employees
                          across the full Sun-Sat weeks covering the range
    include_entry       — predicate: does this entry appear in the report (project
                          filter)? RT/OT is still computed from ALL entries.
    group_lookup        — {(user_id, workspace_id): group value}, only needed when
                          grouping by "group".
    policy_by_workspace — {workspace_id: overtime_policy}; entries of a workspace
                          follow its policy ("envision" / "standard" / none).
                          Omitted = every entry uses the Envision policy.
    Returns {group_key_tuple: {bucket_idx: [total, regular, overtime]}}.
    """
    week_start, week_end = week_range_for(date_from, date_to)
    stat_holidays = alberta_stat_holidays(range(week_start.year, week_end.year + 1))

    dated = []
    for entry in entries:
        if (entry.duration or 0) <= 0:
            continue
        local_date = utc_to_envision_local(entry.start_time).date()
        if week_start <= local_date <= week_end:
            dated.append((entry, local_date))
    # Chronological, so Regular hours are used up by earlier work and OT lands on later work.
    dated.sort(key=lambda item: (item[0].start_time, str(item[0].user_id), item[0].created_at, str(item[0].id)))

    envision_daily_used = defaultdict(float)   # (user, day)            -> Regular hours used
    envision_weekly_used = defaultdict(float)  # (user, week's Sunday)  -> Regular hours used
    standard_used = defaultdict(float)         # (user, day, project)   -> Regular hours used

    def regular_hours(policy, entry, day, hours):
        """Regular part of `hours` worked now under `policy`; consumes the matching allowances."""
        if policy == "envision":
            if day in stat_holidays:
                return 0.0  # statutory holiday: every hour is OT and none of it uses up the weekly Regular allowance
            day_key = (entry.user_id, day)
            week_key = (entry.user_id, _week_sunday(day))
            room = max(
                min(ENVISION_DAILY_RT - envision_daily_used[day_key], ENVISION_WEEKLY_RT - envision_weekly_used[week_key]),
                0.0,
            )
            regular = min(hours, room)
            envision_daily_used[day_key] += regular
            envision_weekly_used[week_key] += regular
            return regular
        if policy == "standard":
            project = entry.project
            cap = (
                float(project.default_rt_hours)
                if project is not None and project.default_rt_hours is not None
                else STANDARD_DEFAULT_DAILY_RT
            )
            std_key = (entry.user_id, day, entry.project_id)
            regular = min(hours, max(cap - standard_used[std_key], 0.0))
            standard_used[std_key] += regular
            return regular
        return hours  # no overtime policy: everything is Regular

    cells = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))

    # Entries of one employee that start at the same moment (e.g. a whole-day field ticket split across
    # projects) happened concurrently — there's no real order between them, so they share the Regular /
    # OT split in proportion to their hours instead of the first one arbitrarily taking all the Regular.
    for _key, concurrent in groupby(dated, key=lambda item: (item[0].start_time, item[0].user_id)):
        concurrent = list(concurrent)
        day = concurrent[0][1]

        subgroups = defaultdict(list)
        for entry, _day in concurrent:
            policy = "envision" if policy_by_workspace is None else policy_by_workspace.get(entry.workspace_id)
            subgroups[(policy, entry.project_id if policy == "standard" else None)].append(entry)

        for (policy, _project), group_entries in subgroups.items():
            group_hours = sum(e.duration for e in group_entries) / 60.0
            group_regular = regular_hours(policy, group_entries[0], day, group_hours)

            if not (date_from <= day <= date_to):
                continue  # before/after the range: only needed to advance the daily/weekly allowances
            for entry in group_entries:
                if not include_entry(entry):
                    continue
                hours = entry.duration / 60.0
                regular = group_regular * (hours / group_hours)
                key = tuple(_dim_key(entry, dim, group_lookup) for dim in group_by)
                cell = cells[key][date_to_bucket[day]]
                cell[0] += hours
                cell[1] += regular
                cell[2] += hours - regular

    return cells


# ── Workbook ──────────────────────────────────────────────────────────────────

HEADER_FILL = PatternFill("solid", fgColor="4F81BD")
SUBTOTAL_FILL = PatternFill("solid", fgColor="DCE6F1")
GRAND_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
BOLD_FONT = Font(name="Calibri", bold=True, size=11)
NORMAL_FONT = Font(name="Calibri", size=11)
GRAND_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
TITLE_FONT = Font(name="Calibri", bold=True, size=14, color="1F3864")
NOTE_FONT = Font(name="Calibri", italic=True, size=10, color="595959")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center")
THIN = Side(style="thin", color="000000")
THIN_BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HOURS_FORMAT = '#,##0.00;-#,##0.00;"-"'


def _cell(ws, row, col, value, font=None, fill=None, align=None, border=None, number_format=None):
    c = ws.cell(row=row, column=col, value=value)
    if font:
        c.font = font
    if fill:
        c.fill = fill
    if align:
        c.alignment = align
    if border:
        c.border = border
    if number_format:
        c.number_format = number_format
    return c


def generate_custom_report_xlsx(
    *, cells, buckets, metrics, group_by, summary_lines, include_chart=False, chart_type="bar",
):
    """
    cells         — output of aggregate_hours
    buckets       — output of build_buckets
    metrics       — subset of METRICS_ORDER (already de-duplicated and ordered)
    group_by      — 1 or 2 of GROUP_DIMS
    summary_lines — [(label, value)] shown in the header block (filters used)
    """
    n_group = len(group_by)
    n_metrics = len(metrics)
    m_idx = [METRIC_INDEX[m] for m in metrics]

    wb = Workbook()
    ws = wb.active
    ws.title = "Report"

    # ── Title + filter summary ────────────────────────────────────────────────
    _cell(ws, 1, 1, "Custom Report", font=TITLE_FONT, align=LEFT)
    ws.row_dimensions[1].height = 24
    row = 2
    for label, value in summary_lines:
        _cell(ws, row, 1, label + ":", font=BOLD_FONT, align=LEFT)
        _cell(ws, row, 2, value, font=NORMAL_FONT, align=LEFT)
        row += 1
    row += 1

    # ── Column headers ────────────────────────────────────────────────────────
    two_row_header = n_metrics > 1
    h1 = row
    h2 = row + 1 if two_row_header else row
    first_body_row = h2 + 1
    bucket_col = lambda i, j: n_group + 1 + i * n_metrics + j  # noqa: E731
    total_col = n_group + 1 + len(buckets) * n_metrics

    for g, dim in enumerate(group_by, start=1):
        _cell(ws, h1, g, GROUP_LABELS[dim], font=HEADER_FONT, fill=HEADER_FILL, align=CENTER, border=THIN_BORDER)
        if two_row_header:
            _cell(ws, h2, g, None, fill=HEADER_FILL, border=THIN_BORDER)
            ws.merge_cells(start_row=h1, start_column=g, end_row=h2, end_column=g)
        ws.column_dimensions[get_column_letter(g)].width = 28

    def header_block(start_col, text):
        if two_row_header:
            for j, metric in enumerate(metrics):
                _cell(ws, h1, start_col + j, None, fill=HEADER_FILL, border=THIN_BORDER)
                _cell(ws, h2, start_col + j, METRIC_LABELS[metric], font=HEADER_FONT, fill=HEADER_FILL,
                      align=CENTER, border=THIN_BORDER)
            _cell(ws, h1, start_col, text, font=HEADER_FONT, fill=HEADER_FILL, align=CENTER, border=THIN_BORDER)
            ws.merge_cells(start_row=h1, start_column=start_col, end_row=h1, end_column=start_col + n_metrics - 1)
        else:
            _cell(ws, h1, start_col, text, font=HEADER_FONT, fill=HEADER_FILL, align=CENTER, border=THIN_BORDER)

    for i, bucket in enumerate(buckets):
        header_block(bucket_col(i, 0), bucket["label"])
    header_block(total_col, "Total" if two_row_header else "Total ({})".format(METRIC_LABELS[metrics[0]]))

    for c in range(n_group + 1, total_col + n_metrics):
        ws.column_dimensions[get_column_letter(c)].width = 12 if n_metrics == 1 else 11
    ws.row_dimensions[h1].height = 32
    if two_row_header:
        ws.row_dimensions[h2].height = 18

    # ── Body ──────────────────────────────────────────────────────────────────
    def vector(bucket_map):
        """Per-bucket [t, r, o] list (zeros where empty) + range totals."""
        per_bucket = [list(bucket_map.get(i, (0.0, 0.0, 0.0))) for i in range(len(buckets))]
        totals = [sum(b[k] for b in per_bucket) for k in range(3)]
        return per_bucket, totals

    def write_row(r, labels, per_bucket, totals, font, fill):
        for g, text in enumerate(labels, start=1):
            _cell(ws, r, g, text, font=font, fill=fill, align=LEFT, border=THIN_BORDER)
        for i, b in enumerate(per_bucket):
            for j, k in enumerate(m_idx):
                _cell(ws, r, bucket_col(i, j), round(b[k], 2), font=font, fill=fill, align=CENTER,
                      border=THIN_BORDER, number_format=HOURS_FORMAT)
        for j, k in enumerate(m_idx):
            _cell(ws, r, total_col + j, round(totals[k], 2), font=BOLD_FONT if fill is None else font,
                  fill=fill, align=CENTER, border=THIN_BORDER, number_format=HOURS_FORMAT)
        ws.row_dimensions[r].height = 16

    def add_into(acc, per_bucket):
        for i, b in enumerate(per_bucket):
            for k in range(3):
                acc[i][k] += b[k]

    sort_key = lambda key_el: (key_el[0].lower(), key_el[1])  # noqa: E731
    grand = [[0.0, 0.0, 0.0] for _ in buckets]
    r = first_body_row

    if n_group == 1:
        for key in sorted(cells, key=lambda k: sort_key(k[0])):
            per_bucket, totals = vector(cells[key])
            write_row(r, [key[0][0]], per_bucket, totals, NORMAL_FONT, None)
            add_into(grand, per_bucket)
            r += 1
    else:
        for primary in sorted({k[0] for k in cells}, key=sort_key):
            block = [[0.0, 0.0, 0.0] for _ in buckets]
            for key in sorted((k for k in cells if k[0] == primary), key=lambda k: sort_key(k[1])):
                per_bucket, totals = vector(cells[key])
                write_row(r, [key[0][0], key[1][0]], per_bucket, totals, NORMAL_FONT, None)
                add_into(block, per_bucket)
                r += 1
            block_totals = [sum(b[k] for b in block) for k in range(3)]
            write_row(r, ["{} - Total".format(primary[0]), ""], block, block_totals, BOLD_FONT, SUBTOTAL_FILL)
            add_into(grand, block)
            r += 1

    grand_totals = [sum(b[k] for b in grand) for k in range(3)]
    write_row(r, ["GRAND TOTAL"] + [""] * (n_group - 1), grand, grand_totals, GRAND_FONT, GRAND_FILL)

    ws.freeze_panes = ws.cell(row=first_body_row, column=n_group + 1)

    if "regular" in metrics or "overtime" in metrics:
        _cell(ws, r + 2, 1,
              "Regular/Overtime follows each workspace's overtime policy. Envision: an Alberta statutory holiday is "
              "all Overtime; on other days the first 8h is Regular and anything beyond is Overtime, and once a "
              "Sun-Sat week's Regular hours reach 44 the remaining hours are Overtime. Calculated from each employee's "
              "total hours across all projects, assigned to projects in the order the time was worked (time worked "
              "at the same moment is shared in proportion to hours).",
              font=NOTE_FONT, align=LEFT)

    if include_chart:
        _add_chart_sheet(wb, cells, buckets, metrics, group_by, chart_type)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ── Chart ─────────────────────────────────────────────────────────────────────

def _add_chart_sheet(wb, cells, buckets, metrics, group_by, chart_type):
    """A 'Chart' sheet: the chart's source data in A1.., the native (editable)
    Excel chart to its right. bar/pie compare the first grouping level's
    totals; line shows each period's grand total over time."""
    ws = wb.create_sheet("Chart")
    m_idx = [METRIC_INDEX[m] for m in metrics]
    primary_label = GROUP_LABELS[group_by[0]]

    if chart_type == "line":
        title = "Hours over time"
        category_header = "Period"
        rows = []
        for i, bucket in enumerate(buckets):
            totals = [0.0, 0.0, 0.0]
            for bucket_map in cells.values():
                cell = bucket_map.get(i)
                if cell:
                    for k in range(3):
                        totals[k] += cell[k]
            rows.append((bucket["label"], totals))
        series_metrics = metrics
        series_idx = m_idx
    else:
        by_primary = defaultdict(lambda: [0.0, 0.0, 0.0])
        for key, bucket_map in cells.items():
            for cell in bucket_map.values():
                for k in range(3):
                    by_primary[key[0]][k] += cell[k]
        if chart_type == "pie":
            # A pie holds one series: Total if it was selected, else the first metric chosen.
            pie_metric = "total" if "total" in metrics else metrics[0]
            series_metrics, series_idx = [pie_metric], [METRIC_INDEX[pie_metric]]
        else:
            series_metrics, series_idx = metrics, m_idx
        ranked = sorted(by_primary.items(), key=lambda kv: -kv[1][series_idx[0]])[:MAX_CHART_CATEGORIES]
        rows = [(key[0], totals) for key, totals in ranked]
        title = "Hours by {}".format(primary_label.lower())
        if len(by_primary) > MAX_CHART_CATEGORIES:
            title += " (top {})".format(MAX_CHART_CATEGORIES)
        category_header = primary_label

    _cell(ws, 1, 1, category_header, font=HEADER_FONT, fill=HEADER_FILL, align=CENTER, border=THIN_BORDER)
    for j, metric in enumerate(series_metrics, start=2):
        _cell(ws, 1, j, "{} Hours".format(METRIC_LABELS[metric]), font=HEADER_FONT, fill=HEADER_FILL,
              align=CENTER, border=THIN_BORDER)
    for r, (label, totals) in enumerate(rows, start=2):
        _cell(ws, r, 1, label, font=NORMAL_FONT, align=LEFT, border=THIN_BORDER)
        for j, k in enumerate(series_idx, start=2):
            _cell(ws, r, j, round(totals[k], 2), font=NORMAL_FONT, align=CENTER, border=THIN_BORDER,
                  number_format=HOURS_FORMAT)
    ws.column_dimensions["A"].width = 30
    for j in range(2, 2 + len(series_metrics)):
        ws.column_dimensions[get_column_letter(j)].width = 16

    n = len(rows)
    data = Reference(ws, min_col=2, max_col=1 + len(series_metrics), min_row=1, max_row=1 + n)
    cats = Reference(ws, min_col=1, min_row=2, max_row=1 + n)

    if chart_type == "pie":
        chart = PieChart()
        chart.dataLabels = DataLabelList()
        chart.dataLabels.showPercent = True
    elif chart_type == "line":
        chart = LineChart()
    else:
        chart = BarChart()
        chart.type = "col"
        chart.grouping = "clustered"

    chart.title = title
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    chart.width, chart.height = 26, 13
    if chart_type != "pie":
        chart.y_axis.title = "Hours"
        chart.x_axis.title = category_header
        # openpyxl >= 3.1 hides axes unless explicitly un-deleted.
        chart.x_axis.delete = False
        chart.y_axis.delete = False
    ws.add_chart(chart, "{}2".format(get_column_letter(3 + len(series_metrics))))
