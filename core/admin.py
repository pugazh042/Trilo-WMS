from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth import get_user_model

from .models import (
    Carrier,
    Consolidation,
    GoodsReceipt,
    InboundPutawayTask,
    Location,
    Order,
    OrderItem,
    Packing,
    PickTask,
    PurchaseOrder,
    PurchaseOrderItem,
    QCCheck,
    Return,
    Shipment,
    ShipmentItem,
    SKU,
    StockBin,
    Task,
    TruckAssignment,
    StagingItem,
)

User = get_user_model()


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("WMS", {"fields": ("role", "employee_id", "warehouse")}),
    )
    list_display = ("username", "email", "employee_id", "role", "is_active", "is_staff")
    search_fields = ("username", "email", "employee_id")


admin.site.register(SKU)
admin.site.register(Location)
admin.site.register(StockBin)
admin.site.register(Order)
admin.site.register(OrderItem)
admin.site.register(Task)
admin.site.register(PickTask)
admin.site.register(Consolidation)
admin.site.register(Packing)
admin.site.register(Shipment)
admin.site.register(Carrier)
admin.site.register(ShipmentItem)
admin.site.register(TruckAssignment)
admin.site.register(Return)
admin.site.register(PurchaseOrder)
admin.site.register(PurchaseOrderItem)
admin.site.register(GoodsReceipt)
admin.site.register(QCCheck)
admin.site.register(StagingItem)
admin.site.register(InboundPutawayTask)
