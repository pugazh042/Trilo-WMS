from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MANAGER = "manager", "Manager"
        PICKER = "picker", "Picker"
        PUTAWAY = "putaway", "Putaway"
        PACKER = "packer", "Packer"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PICKER)
    employee_id = models.CharField(max_length=32, unique=True)
    warehouse = models.CharField(max_length=120, blank=True, null=True)
    profile_photo = models.ImageField(upload_to="profiles/", blank=True, null=True)
    assigned_warehouse = models.ForeignKey(
        "Warehouse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workers",
    )

    def __str__(self) -> str:
        return f"{self.get_full_name() or self.username} ({self.employee_id})"


class SKU(models.Model):
    class UnitType(models.TextChoices):
        UNIT = "unit", "Unit"
        BOX = "box", "Box"
        PALLET = "pallet", "Pallet"

    class ABCClass(models.TextChoices):
        A = "A", "A (Fast)"
        B = "B", "B (Medium)"
        C = "C", "C (Slow)"

    name = models.CharField(max_length=200)
    sku_code = models.CharField(max_length=80, unique=True)
    category = models.CharField(max_length=120, blank=True, default="")
    abc_class = models.CharField(max_length=1, choices=ABCClass.choices, default=ABCClass.C)
    brand = models.CharField(max_length=120, blank=True, default="")
    unit_type = models.CharField(max_length=20, choices=UnitType.choices, default=UnitType.UNIT)
    weight = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    dimensions = models.CharField(max_length=120, blank=True, default="")
    barcode = models.CharField(max_length=120, blank=True, default="")
    quantity = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.sku_code} - {self.name}"


class Order(models.Model):
    """Business order_id (e.g. ORD-ABC123) + workflow status."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ALLOCATED = "allocated", "Allocated"
        PICKING = "picking", "Picking"
        CONSOLIDATING = "consolidating", "Consolidating"
        PACKING = "packing", "Packing"
        SHIPPED = "shipped", "Shipped"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class AllocationStatus(models.TextChoices):
        NOT_ALLOCATED = "not_allocated", "Not Allocated"
        PARTIALLY_ALLOCATED = "partially_allocated", "Partially Allocated"
        FULLY_ALLOCATED = "fully_allocated", "Fully Allocated"

    order_id = models.CharField(max_length=40, unique=True, db_index=True)
    customer_name = models.CharField(max_length=200)
    customer_phone = models.CharField(max_length=40, blank=True, default="")
    delivery_address = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    allocation_status = models.CharField(
        max_length=30, choices=AllocationStatus.choices, default=AllocationStatus.NOT_ALLOCATED
    )
    on_hold = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.order_id:
            import secrets

            self.order_id = f"ORD-{secrets.token_hex(4).upper()}"
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.order_id} — {self.customer_name}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="order_items")
    quantity = models.PositiveIntegerField()
    allocated_quantity = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.order.order_id} — {self.sku.sku_code} x {self.quantity}"


class Task(models.Model):
    class Type(models.TextChoices):
        PICKING = "picking", "Picking"
        PACKING = "packing", "Packing"
        PUTAWAY = "putaway", "Putaway"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        DONE = "done", "Done"

    type = models.CharField(max_length=20, choices=Type.choices)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.type} ({self.status})"


class Location(models.Model):
    code = models.CharField(max_length=40, unique=True)
    zone = models.CharField(max_length=40, blank=True, default="")
    aisle = models.CharField(max_length=40, blank=True, default="")
    rack = models.CharField(max_length=40, blank=True, default="")
    bin = models.CharField(max_length=40, blank=True, default="")

    def __str__(self) -> str:
        return self.code


class StockBin(models.Model):
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="stock_bins")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="stock_bins")
    on_hand = models.PositiveIntegerField(default=0)
    reserved = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("sku", "location")

    @property
    def available(self) -> int:
        return max(0, int(self.on_hand) - int(self.reserved))

    def __str__(self) -> str:
        return f"{self.sku.sku_code}@{self.location.code}"


class PickTask(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        EXCEPTION = "exception", "Exception"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="pick_tasks")
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="pick_tasks")
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="pick_tasks")
    stock_bin = models.ForeignKey(StockBin, on_delete=models.PROTECT, related_name="pick_tasks")
    quantity = models.PositiveIntegerField()
    source_location = models.CharField(max_length=80, blank=True, default="")
    picker = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="pick_tasks"
    )
    picked_qty = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    issue_note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"PT{self.pk} {self.order.order_id} {self.sku.sku_code} x {self.quantity}"


class Consolidation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        READY_FOR_PACKING = "ready_for_packing", "Ready for Packing"
        DONE = "done", "Done"

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="consolidation")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Consolidation {self.order.order_id} ({self.status})"


class Packing(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="packings")
    box_type = models.CharField(max_length=80, blank=True, default="")
    packed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="packings"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    verification_status = models.BooleanField(default=False)
    is_box_selected = models.BooleanField(default=False)
    label_text = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Packing {self.order.order_id} ({self.status})"


class Carrier(models.Model):
    name = models.CharField(max_length=120)
    contact = models.CharField(max_length=120, blank=True, default="")
    service_type = models.CharField(max_length=80, blank=True, default="")

    def __str__(self) -> str:
        return self.name


class Shipment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        READY = "ready", "Ready"
        DISPATCHED = "dispatched", "Dispatched"
        DELIVERED = "delivered", "Delivered"

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="shipment")
    carrier = models.CharField(max_length=120, blank=True, default="")
    carrier_partner = models.ForeignKey(
        Carrier, on_delete=models.SET_NULL, null=True, blank=True, related_name="shipments"
    )
    tracking_number = models.CharField(max_length=120, blank=True, default="")
    truck_ref = models.CharField(max_length=120, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    estimated_delivery = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"SHIP {self.order.order_id} ({self.status})"


class ShipmentItem(models.Model):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="items")
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="shipment_items")
    quantity = models.PositiveIntegerField()

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.shipment_id} {self.sku.sku_code} x {self.quantity}"


class TruckAssignment(models.Model):
    shipment = models.OneToOneField(Shipment, on_delete=models.CASCADE, related_name="truck")
    truck_number = models.CharField(max_length=40, blank=True, default="")
    driver_name = models.CharField(max_length=120, blank=True, default="")
    driver_contact = models.CharField(max_length=120, blank=True, default="")
    dock_number = models.CharField(max_length=40, blank=True, default="")


class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        CREATED = "created", "Created"
        ARRIVED = "arrived", "Arrived"
        RECEIVING = "receiving", "Receiving"
        QC = "qc", "QC"
        COMPLETED = "completed", "Completed"

    po_number = models.CharField(max_length=40, unique=True, db_index=True)
    supplier_name = models.CharField(max_length=200)
    expected_delivery_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED)
    dock_number = models.CharField(max_length=40, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.po_number:
            import secrets

            self.po_number = f"PO-{secrets.token_hex(3).upper()}"
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.po_number} — {self.supplier_name}"


class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="po_lines")
    expected_qty = models.PositiveIntegerField()
    received_qty = models.PositiveIntegerField(default=0)
    received_batch = models.CharField(max_length=80, blank=True, default="")

    class Meta:
        unique_together = ("purchase_order", "sku")

    def __str__(self) -> str:
        return f"{self.purchase_order.po_number} {self.sku.sku_code}"


class GoodsReceipt(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"

    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="receipts")
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="goods_receipts"
    )
    received_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)

    def __str__(self) -> str:
        return f"GR{self.pk} {self.purchase_order.po_number}"


class QCCheck(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    goods_receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="qc_checks")
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="qc_checks")
    received_qty = models.PositiveIntegerField(default=0)
    passed_qty = models.PositiveIntegerField(default=0)
    failed_qty = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    remarks = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("goods_receipt", "sku")

    def __str__(self) -> str:
        return f"QC {self.goods_receipt_id} {self.sku.sku_code}"


class StagingItem(models.Model):
    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting"
        READY_FOR_PUTAWAY = "ready_for_putaway", "Ready for Put-away"
        CLEARED = "cleared", "Cleared"

    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="staging_items")
    quantity = models.PositiveIntegerField()
    staging_zone = models.CharField(max_length=80, default="STG-A")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.WAITING)
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, null=True, blank=True, related_name="staging_items"
    )

    def __str__(self) -> str:
        return f"{self.sku.sku_code} x {self.quantity} @ {self.staging_zone}"


class InboundPutawayTask(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        DONE = "done", "Done"

    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="inbound_putaway_tasks")
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="inbound_putaway_tasks")
    quantity = models.PositiveIntegerField()
    suggested_location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name="suggested_inbound_tasks")
    suggested_bin = models.ForeignKey("Bin", on_delete=models.SET_NULL, null=True, blank=True, related_name="suggested_putaway_tasks")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inbound_putaway_tasks",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"IPW{self.pk} {self.purchase_order.po_number} {self.sku.sku_code}"


class Return(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        RECEIVED = "received", "Received"
        INSPECTED = "inspected", "Inspected"
        RESTOCKED = "restocked", "Restocked"
        REJECTED = "rejected", "Rejected"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="returns")
    reason = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    inspection_notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Return {self.order.order_id} ({self.status})"


class Warehouse(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20, unique=True)
    location = models.CharField(max_length=255, blank=True, default="")
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_warehouses",
    )
    contact = models.CharField(max_length=120, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class Zone(models.Model):
    class Type(models.TextChoices):
        BULK = "bulk", "Bulk"
        DISCRETE = "discrete", "Discrete"

    class Temperature(models.TextChoices):
        NORMAL = "normal", "Normal"
        COLD = "cold", "Cold"

    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="zones")
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20)
    category = models.CharField(max_length=120, blank=True, default="")
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.BULK)
    temperature = models.CharField(
        max_length=20, choices=Temperature.choices, default=Temperature.NORMAL
    )
    is_hazardous = models.BooleanField(default=False)

    class Meta:
        unique_together = ("warehouse", "code")

    def __str__(self) -> str:
        return f"{self.warehouse.code}-{self.code}"


class Aisle(models.Model):
    zone = models.ForeignKey(Zone, on_delete=models.CASCADE, related_name="aisles")
    aisle_number = models.CharField(max_length=20)

    class Meta:
        unique_together = ("zone", "aisle_number")

    def __str__(self) -> str:
        return f"{self.zone.warehouse.code}-{self.zone.code}-A{self.aisle_number}"


class Rack(models.Model):
    aisle = models.ForeignKey(Aisle, on_delete=models.CASCADE, related_name="racks")
    rack_number = models.CharField(max_length=20)
    levels = models.PositiveIntegerField(default=1)
    total_levels = models.PositiveIntegerField(default=1)
    max_capacity = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("aisle", "rack_number")

    def __str__(self) -> str:
        return f"{self.aisle.zone.warehouse.code}-{self.aisle.zone.code}-A{self.aisle.aisle_number}-R{self.rack_number}"


class Bin(models.Model):
    class Size(models.TextChoices):
        SMALL = "S", "Small"
        MEDIUM = "M", "Medium"
        LARGE = "L", "Large"

    class BinType(models.TextChoices):
        PALLET = "pallet", "Pallet"
        SHELF = "shelf", "Shelf"
        CARTON = "carton", "Carton"
        LOOSE = "loose", "Loose"

    rack = models.ForeignKey(Rack, on_delete=models.CASCADE, related_name="bins")
    level = models.ForeignKey("Level", on_delete=models.CASCADE, null=True, blank=True, related_name="bins")
    level_number = models.PositiveIntegerField(default=1)
    bin_code = models.CharField(max_length=80, unique=True)
    size = models.CharField(max_length=1, choices=Size.choices, default=Size.MEDIUM)
    bin_type = models.CharField(max_length=20, choices=BinType.choices, default=BinType.SHELF)
    allow_mixed_skus = models.BooleanField(default=False)
    max_capacity = models.PositiveIntegerField(default=0)
    current_capacity = models.PositiveIntegerField(default=0)
    max_weight = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    max_volume = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self) -> str:
        return self.bin_code


class Inventory(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        DAMAGED = "damaged", "Damaged"

    sku = models.ForeignKey(SKU, on_delete=models.CASCADE, related_name="inventory_records")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="inventory_records")
    zone = models.ForeignKey(Zone, on_delete=models.SET_NULL, null=True, blank=True, related_name="inventory_records")
    bin = models.ForeignKey(Bin, on_delete=models.PROTECT, null=True, blank=True, related_name="inventory_records")
    quantity = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    batch_number = models.CharField(max_length=80, blank=True, default="")
    expiry_date = models.DateField(null=True, blank=True)

    class Meta:
        unique_together = ("sku", "warehouse", "bin", "batch_number")

    def __str__(self) -> str:
        return f"{self.sku.sku_code} @ {self.warehouse.code}"


class Level(models.Model):
    rack = models.ForeignKey(Rack, on_delete=models.CASCADE, related_name="level_rows")
    level_number = models.PositiveIntegerField()

    class Meta:
        unique_together = ("rack", "level_number")
        ordering = ["level_number"]

    def __str__(self) -> str:
        return f"{self.rack.aisle.zone.warehouse.code}-{self.rack.aisle.zone.code}-A{self.rack.aisle.aisle_number}-R{self.rack.rack_number}-L{self.level_number}"


class StockAdjustment(models.Model):
    class AdjustmentType(models.TextChoices):
        ADD = "add", "Add"
        REMOVE = "remove", "Remove"

    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="stock_adjustments")
    bin = models.ForeignKey(Bin, on_delete=models.PROTECT, related_name="stock_adjustments")
    adjustment_type = models.CharField(max_length=20, choices=AdjustmentType.choices)
    quantity = models.PositiveIntegerField()
    reason = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="stock_adjustments")
    timestamp = models.DateTimeField(auto_now_add=True)

class StockLedger(models.Model):
    class MovementType(models.TextChoices):
        PUTAWAY = "putaway", "Putaway"
        PICK = "pick", "Pick"
        TRANSFER = "transfer", "Transfer"
        ADJUSTMENT = "adjustment", "Adjustment"
        RECEIPT = "receipt", "Receipt"

    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="ledger_entries")
    movement_type = models.CharField(max_length=30, choices=MovementType.choices)
    quantity = models.IntegerField()  # positive for IN, negative for OUT
    from_bin = models.ForeignKey(Bin, on_delete=models.SET_NULL, null=True, blank=True, related_name="ledger_out_entries")
    to_bin = models.ForeignKey(Bin, on_delete=models.SET_NULL, null=True, blank=True, related_name="ledger_in_entries")
    reference_id = models.CharField(max_length=80, blank=True, default="", help_text="e.g. Task ID, Order ID")
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

class ReplenishmentTask(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        DONE = "done", "Done"

    sku = models.ForeignKey(SKU, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    source_bin = models.ForeignKey(Bin, on_delete=models.CASCADE, related_name="replenishment_sources")
    destination_bin = models.ForeignKey(Bin, on_delete=models.CASCADE, related_name="replenishment_destinations")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

