import uuid
from decimal import Decimal
from django.db import models

from time_entries.models import TimeEntry
from workspaces.models import Workspace

class OrganizationAsset(models.Model):
    CHARGE_TYPE = (
        ("hourly", "Per Hour"),
        ("quantity", "By Quantity"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    charge_type = models.CharField(max_length=20, choices=CHARGE_TYPE)

    # If charge_type = hourly
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # If charge_type = quantity
    quantity_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Inventory quantity for quantity-based assets
    total_quantity = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.get_charge_type_display()})"


class AssetUsage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    time_entry = models.ForeignKey(TimeEntry, on_delete=models.CASCADE, related_name="asset_usages")
    asset = models.ForeignKey(OrganizationAsset, on_delete=models.CASCADE)

    # Only used for quantity type
    quantity_used = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # calculated after saving

    def calculate_cost(self, duration_hours):
        """Calculate cost depending on charge type."""
        if self.asset.charge_type == "hourly":
            return Decimal(self.asset.hourly_rate) * Decimal(duration_hours)
        return Decimal(self.quantity_used or 0) * Decimal(self.asset.quantity_rate)