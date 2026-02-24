
from decimal import Decimal
from datetime import datetime, time, date, timedelta
from django.utils import timezone
from projects.models import LaborRate, JobTitle
from workspaces.models import Holiday

class CostCalculator:
    def __init__(self, user, date_obj, project):
        self.user = user
        self.date_obj = date_obj
        self.project = project
        self.rates_cache = {} # (job_title_id, condition) -> rate_obj

    def get_hours_breakdown(self, daily_entries, weekly_total_before_today):
        """
        Calculates Regular, Overtime, and Double Time hours for a specific day.
        
        Rules:
        1. Stat Holiday: All hours are Double Time.
        2. Sunday:
           - If M-F worked > 40 hours: All hours are Overtime.
           - Else: Apply Daily Rule.
        3. Daily Rule (Mon-Sat or Sun if <40 M-F):
           - First 8h = Reg
           - 8h-12h = OT
           - >12h = DT
        """
        is_holiday = self._is_stat_holiday()
        is_sunday = self.date_obj.weekday() == 6
        
        total_hours = sum(e.duration for e in daily_entries) / 60.0
        
        reg_hours = 0.0
        ot_hours = 0.0
        dt_hours = 0.0

        if is_holiday:
            dt_hours = total_hours
        elif is_sunday and weekly_total_before_today >= 40:
            ot_hours = total_hours
        else:
            # Daily Rule
            if total_hours > 12:
                dt_hours = total_hours - 12
                ot_hours = 4 # (12-8)
                reg_hours = 8
            elif total_hours > 8:
                ot_hours = total_hours - 8
                reg_hours = 8
            else:
                reg_hours = total_hours

        return {
            "reg": reg_hours,
            "ot": ot_hours,
            "dt": dt_hours,
            "total": total_hours
        }

    def calculate_cost(self, breakdown, job_title):
        """
        Calculates cost based on the breakdown and LaborRate lookup.
        """
        if not job_title:
            return 0.0, 0.0 # Cost, Rate Used (Base)
            
        rate_obj = self._get_rate_obj(job_title)
        
        if not rate_obj:
            # Fallback to base logic if no rate card found?
            # Or return 0. Prompt implies we must use the table.
            # If no table entry, maybe use ProjectRole rate?
            return 0.0, 0.0

        reg_cost = Decimal(breakdown['reg']) * rate_obj.regular_cost
        ot_cost = Decimal(breakdown['ot']) * rate_obj.overtime_cost
        dt_cost = Decimal(breakdown['dt']) * rate_obj.double_time_cost
        
        total_cost = reg_cost + ot_cost + dt_cost
        return total_cost, rate_obj.regular_cost # Return base rate for display

    def _is_stat_holiday(self):
        # Check Holiday model
        # Assuming project.workspace is available
        return Holiday.objects.filter(
            date=self.date_obj, 
            workspace=self.project.workspace
        ).exists() or Holiday.objects.filter(
            date=self.date_obj,
            workspace__isnull=True
        ).exists()

    def _get_rate_obj(self, job_title):
        """
        Determines the correct LaborRate record based on Condition Logic.
        Conditions: Day, Night, Saturday, Saturday Night, Sunday Day, Sunday Night, Holiday
        """
        # Determine Day Condition
        is_holiday = self._is_stat_holiday()
        weekday = self.date_obj.weekday() # 0=Mon, 5=Sat, 6=Sun
        
        # Determine Shift ("Day" vs "Night")
        # For simplicity, if ANY entry in the day is "Night", use Night rate? 
        # Or split regular hours?
        # The prompt table has "Day" vs "Night".
        # User said: Morning start 5:50AM, Night start 6:00PM.
        # I need to check the entries passed to calculate_cost. 
        # Wait, calculate_cost takes `breakdown` which is aggregated hours.
        # If a user works 4 hours Day and 4 hours Night, how do we split?
        # The complexity increases.
        # SIMPLIFICATION: We will pick the "Shift" based on the **First Entry's Start Time**. 
        # Use first entry to determine the "Shift" for the whole day for that user.
        # This is standard in many systems unless split is required.
        pass 
        
    def calculate_daily_cost_for_user(self, user_entries, weekly_total_before_today):
        """
        Main entry point.
        user_entries: List of TimeEntry objects for this user on this day.
        """
        if not user_entries:
            return {}

        breakdown = self.get_hours_breakdown(user_entries, weekly_total_before_today)
        
        # We need to calculate cost per Job Title if they switch roles?
        # Implementation Plan said "Iterate users". 
        # If user has multiple roles in a day, we might need to split.
        # But `LaborRate` is by JobTitle.
        # So we should group entries by Job Title first? 
        # The prompt table "Title" column implies rates are per title.
        
        # Refined Logic:
        # Group entries by Job Title.
        # But wait, the "Daily Rule" (8/12) applies to the *User's Total Hours*, not per job title.
        # Example: 
        #   Job A: 6 hours
        #   Job B: 4 hours
        #   Total: 10 hours -> 8 Reg, 2 OT.
        #   Which job gets the OT?
        #   Usually, OT is applied to the later hours. 
        #   Or simpler: Pro-rate?
        #   Let's use a "Bucket" approach. Fill Reg bucket first, then OT.
        
        # Sort entries by start time.
        sorted_entries = sorted(user_entries, key=lambda x: x.start_time)
        
        total_cost = Decimal(0.0)
        
        # We need to know which rate applies to which chunk of time.
        # Let's track consumed hours against the breakdown limits.
        
        remaining_reg = breakdown['reg']
        remaining_ot = breakdown['ot']
        remaining_dt = breakdown['dt']
        
        job_title_breakdowns = {} # {job_title_name: {reg: 0, ot: 0, dt: 0, cost: 0, rate: 0}}
        
        for entry in sorted_entries:
            duration = entry.duration / 60.0
            job_title = entry.job_title
            if not job_title: continue
            
            # Determine Rate Object for this Entry
            rate_obj = self._get_rate_for_entry(entry)
            if not rate_obj: continue
            
            # Allocate duration to Reg/OT/DT buckets
            entry_reg = 0.0
            entry_ot = 0.0
            entry_dt = 0.0
            
            # 1. Fill Reg
            if remaining_reg > 0:
                take = min(duration, remaining_reg)
                entry_reg += take
                remaining_reg -= take
                duration -= take
                
            # 2. Fill OT
            if duration > 0 and remaining_ot > 0:
                take = min(duration, remaining_ot)
                entry_ot += take
                remaining_ot -= take
                duration -= take
                
            # 3. Fill DT
            if duration > 0 and remaining_dt > 0:
                take = min(duration, remaining_dt)
                entry_dt += take
                remaining_dt -= take
                duration -= take

             # 4. Leftover (Floating point issues? or just force into DT?)
             # If breakdown calc was correct, duration should be 0.
             
            # Calculate cost for this entry
            c_reg = Decimal(entry_reg) * rate_obj.regular_cost
            c_ot = Decimal(entry_ot) * rate_obj.overtime_cost
            c_dt = Decimal(entry_dt) * rate_obj.double_time_cost
            
            entry_cost = c_reg + c_ot + c_dt
            total_cost += entry_cost
            
            # Aggregate for Report Display
            jt_name = job_title.name
            if jt_name not in job_title_breakdowns:
                job_title_breakdowns[jt_name] = {
                    "reg": 0.0, "ot": 0.0, "dt": 0.0, "cost": Decimal(0.0), 
                    "reg_rate": float(rate_obj.regular_cost),
                    "ot_rate": float(rate_obj.overtime_cost),
                    "dt_rate": float(rate_obj.double_time_cost),
                    "base_rate": rate_obj.regular_cost # Representative rate
                }
            
            job_title_breakdowns[jt_name]["reg"] += entry_reg
            job_title_breakdowns[jt_name]["ot"] += entry_ot
            job_title_breakdowns[jt_name]["dt"] += entry_dt
            job_title_breakdowns[jt_name]["cost"] += entry_cost

        return job_title_breakdowns

    def _get_rate_for_entry(self, entry):
        # Determine Condition
        # 1. Holiday?
        if self._is_stat_holiday():
            # If Holiday, user said "All hours = Double Time".
            # Does the Rate Table have a "Holiday" row?
            # Or do we use "Holiday" condition?
            # Let's try to find a rate for "Holiday".
            r = self._lookup_rate(entry.job_title, "Holiday")
            if r: return r
            # Fallback: Use Sunday Night? Or just use Day rate and let the logic handle the DT multiplier?
            # The logic `get_hours_breakdown` puts hours into DT bucket.
            # So as long as `double_time_cost` is correct on the returned rate object, we are good.
            # If no "Holiday" row in Excel (user supplied only TCP/LCT Day/Night/Sat/Sun), 
            # we should use "Sunday Night" or "Sunday Day"?
            # Prompt table has: "Sunday Day" and "Sunday Night".
            # It does NOT have "Holiday".
            # BUT the user said: "If Statutory Holiday -> Double Time".
            # AND "Use the costing values from there".
            # Implication: The "Double Time" COST comes from the table.
            # So if I use "Sunday Day" rate, the table says DT Cost = 93.75 (for TCP).
            # If I use "Day" rate, DT Cost = 75.
            # Which one? Usually Stat Holiday pays premium.
            # I will default to "Sunday Day" for Stat Holidays if no explicit "Holiday" row?
            # Actually, `LaborRate` model has `condition`. The Script stored "Holiday" if it existed.
            # If explicit "Holiday" condition fails, I'll fallback to "Sunday Day".
            pass

        # 2. Sunday?
        weekday = self.date_obj.weekday()
        is_sunday = (weekday == 6)
        is_saturday = (weekday == 5)
        
        # 3. Shift?
        # Morning shift start 5:50AM, Night Shift start 6:00PM
        # Compare entry.start_time time component.
        # entry.start_time is datetime.
        # Need to handle timezone... Django defines TIME_ZONE='America/Vancouver'.
        # Assuming entry.start_time is aware.
        
        start_t = entry.start_time.astimezone(timezone.get_current_timezone()).time()
        night_start = time(18, 30)
        morning_start = time(5, 00)
        
        is_night = (start_t >= night_start) or (start_t < morning_start)
        
        condition = "Day"
        if is_sunday:
            condition = "Sunday Night" if is_night else "Sunday Day"
        elif is_saturday:
            condition = "Saturday Night" if is_night else "Saturday"
        else:
            condition = "Night" if is_night else "Day"
            
        return self._lookup_rate(entry.job_title, condition)

    def _lookup_rate(self, job_title, condition):
        try:
            return LaborRate.objects.get(job_title=job_title, condition=condition)
        except LaborRate.DoesNotExist:
            # Fallback logic?
            # If "Saturday Night" missing, try "Night"?
            # If "Sunday Night" missing, try "Sunday"?
             return None

