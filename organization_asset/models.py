import uuid
from decimal import Decimal
from django.db import models

from time_entries.models import TimeEntry
from workspaces.models import Workspace
from projects.models import Project

from core.models import SoftDeleteModel


class OrganizationAsset(SoftDeleteModel):
    CHARGE_TYPE = (
        ("hourly", "Per Hour"),
        ("quantity", "By Quantity"),
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assets",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    charge_type = models.CharField(max_length=20, choices=CHARGE_TYPE)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    quantity_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    total_quantity = models.PositiveIntegerField(default=0, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return "{} ({})".format(self.name, self.get_charge_type_display())


class AssetUsage(SoftDeleteModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    time_entry = models.ForeignKey(TimeEntry, on_delete=models.CASCADE, related_name="asset_usages")
    asset = models.ForeignKey(OrganizationAsset, on_delete=models.CASCADE)
    quantity_used = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def calculate_cost(self, duration_hours):
        if self.asset.charge_type == "hourly":
            usage_hours = self.quantity_used if self.quantity_used is not None else Decimal(duration_hours)
            return Decimal(self.asset.hourly_rate) * Decimal(usage_hours)
        return Decimal(self.quantity_used or 0) * Decimal(self.asset.quantity_rate)
