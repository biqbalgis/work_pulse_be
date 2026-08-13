
from django.core.management.base import BaseCommand, CommandError
import openpyxl
from projects.models import LaborRate, JobTitle
from workspaces.models import Workspace
from decimal import Decimal

class Command(BaseCommand):
    help = 'Import Labor Rates from labor_rates.xlsx'

    def add_arguments(self, parser):
        parser.add_argument(
            '--workspace', required=True,
            help='Workspace ID (or exact name) to import/create job titles under — '
                 'JobTitle.name is unique per workspace, not globally.',
        )

    def handle(self, *args, **options):
        workspace_ref = options['workspace']
        workspace = Workspace.objects.filter(id=workspace_ref).first() or \
            Workspace.objects.filter(name=workspace_ref).first()
        if not workspace:
            raise CommandError(f"Workspace not found: {workspace_ref!r}")

        file_path = "labor_rates.xlsx"
        try:
            wb = openpyxl.load_workbook(file_path)
            sheet = wb.active
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Could not load {file_path}: {e}"))
            return

        # Structure of Excel based on user check:
        # Title | Day | Time | Cost
        # TCP | Day | Regular Time | 37.5
        
        # We need to PIVOT this to:
        # JobTitle + Condition -> (Reg, OT, DT)

        data_map = {} # Key: (Title, DayCondition) -> {Reg: x, OT: y, DT: z}

        # Skip header
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
             self.stdout.write(self.style.WARNING("No data in excel"))
             return
             
        header = rows[0] # Assume header is row 0
        
        for row in rows[1:]:
            if not row or not row[0]: continue
            
            title_name = str(row[0]).strip()
            condition = str(row[1]).strip() # Day, Night, Saturday, etc.
            time_type = str(row[2]).strip() # Regular Time, Over Time, Double Time
            cost = row[3]
            
            if cost is None: cost = 0.0

            key = (title_name, condition)
            if key not in data_map:
                data_map[key] = {
                    "regular_cost": 0.0,
                    "overtime_cost": 0.0,
                    "double_time_cost": 0.0
                }
            
            if "Regular" in time_type:
                data_map[key]["regular_cost"] = cost
            elif "Over" in time_type:
                data_map[key]["overtime_cost"] = cost
            elif "Double" in time_type:
                data_map[key]["double_time_cost"] = cost

        # Now save to DB
        count = 0
        for (job_title_name, condition), costs in data_map.items():
            # Find or Create Job Title, scoped to the given workspace
            job_title, _ = JobTitle.objects.get_or_create(workspace=workspace, name=job_title_name)

            # Update or Create Labor Rate
            LaborRate.objects.update_or_create(
                job_title=job_title,
                condition=condition,
                defaults={
                    "regular_cost": costs["regular_cost"],
                    "overtime_cost": costs["overtime_cost"],
                    "double_time_cost": costs["double_time_cost"]
                }
            )
            count += 1
        
        self.stdout.write(self.style.SUCCESS(f'Successfully imported {count} labor rates.'))
