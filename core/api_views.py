from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from .decorators import role_required
from .models import Aisle, Bin, Inventory, Level, Rack, SKU, StockAdjustment, Warehouse, Zone
from .serializers import (
    aisle_serializer,
    bin_serializer,
    inventory_serializer,
    level_serializer,
    rack_serializer,
    sku_serializer,
    warehouse_serializer,
    zone_serializer,
)


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def warehouse_create_api(request):
    row = Warehouse.objects.create(
        name=(request.POST.get("name") or "").strip(),
        code=(request.POST.get("code") or "").strip().upper(),
        location=(request.POST.get("location") or "").strip(),
        manager_id=request.POST.get("manager") or None,
    )
    return JsonResponse({"ok": True, "data": warehouse_serializer(row)})


@login_required
@role_required("admin", "manager")
def warehouse_list_api(request):
    return JsonResponse({"rows": [warehouse_serializer(x) for x in Warehouse.objects.order_by("code")]})


@login_required
@role_required("admin", "manager")
def warehouse_hierarchy_api(request, warehouse_id: int):
    wh = get_object_or_404(Warehouse, pk=warehouse_id)
    zones_payload = []
    for zone in wh.zones.all().order_by("code"):
        aisles_payload = []
        for aisle in zone.aisles.all().order_by("aisle_number"):
            racks_payload = []
            for rack in aisle.racks.all().order_by("rack_number"):
                levels_payload = []
                for level in rack.level_rows.all().order_by("level_number"):
                    bins_payload = []
                    for b in level.bins.all().order_by("bin_code"):
                        inv_rows = Inventory.objects.filter(bin=b).select_related("sku")
                        bins_payload.append(
                            {
                                "id": b.id,
                                "bin_code": b.bin_code,
                                "size": b.size,
                                "bin_type": b.bin_type,
                                "max_capacity": b.max_capacity,
                                "current_capacity": b.current_capacity,
                                "usage_pct": int((b.current_capacity / b.max_capacity) * 100) if b.max_capacity else 0,
                                "inventory": [{"sku": i.sku.sku_code, "qty": i.quantity, "status": i.status} for i in inv_rows],
                            }
                        )
                    levels_payload.append({"id": level.id, "level_number": level.level_number, "bins": bins_payload})
                racks_payload.append({"id": rack.id, "rack_number": rack.rack_number, "total_levels": rack.total_levels, "levels": levels_payload})
            aisles_payload.append({"id": aisle.id, "aisle_number": aisle.aisle_number, "racks": racks_payload})
        zones_payload.append({"id": zone.id, "name": zone.name, "code": zone.code, "type": zone.type, "temperature": zone.temperature, "is_hazardous": zone.is_hazardous, "aisles": aisles_payload})
    return JsonResponse({"warehouse": warehouse_serializer(wh), "zones": zones_payload})


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def zone_create_api(request):
    wh = get_object_or_404(Warehouse, pk=request.POST.get("warehouse_id"))
    row = Zone.objects.create(
        warehouse=wh,
        name=(request.POST.get("name") or "").strip(),
        code=(request.POST.get("code") or "").strip().upper(),
        type=(request.POST.get("type") or Zone.Type.BULK),
        temperature=(request.POST.get("temperature") or Zone.Temperature.NORMAL),
    )
    return JsonResponse({"ok": True, "data": zone_serializer(row)})


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def zone_update_api(request, zone_id: int):
    row = get_object_or_404(Zone, pk=zone_id)
    row.name = (request.POST.get("name") or row.name).strip()
    row.type = request.POST.get("type") or row.type
    row.temperature = request.POST.get("temperature") or row.temperature
    row.save(update_fields=["name", "type", "temperature"])
    return JsonResponse({"ok": True, "data": zone_serializer(row)})


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def zone_delete_api(request, zone_id: int):
    row = get_object_or_404(Zone, pk=zone_id)
    if row.aisles.exists():
        return JsonResponse({"ok": False, "error": "Cannot delete zone with existing aisles."})
    row.delete()
    return JsonResponse({"ok": True})

@login_required
@role_required("admin", "manager")
def zone_list_api(request):
    qs = Zone.objects.order_by("warehouse_id", "code")
    wid = request.GET.get("warehouse_id")
    if wid:
        qs = qs.filter(warehouse_id=wid)
    return JsonResponse({"rows": [zone_serializer(x) for x in qs]})

@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def aisle_create_api(request):
    zone = get_object_or_404(Zone, pk=request.POST.get("zone_id"))
    row = Aisle.objects.create(
        zone=zone,
        aisle_number=(request.POST.get("aisle_number") or "").strip().upper(),
    )
    return JsonResponse({"ok": True, "data": aisle_serializer(row)})

@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def aisle_update_api(request, aisle_id: int):
    row = get_object_or_404(Aisle, pk=aisle_id)
    row.aisle_number = (request.POST.get("aisle_number") or row.aisle_number).strip().upper()
    row.save(update_fields=["aisle_number"])
    return JsonResponse({"ok": True, "data": aisle_serializer(row)})

@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def aisle_delete_api(request, aisle_id: int):
    row = get_object_or_404(Aisle, pk=aisle_id)
    if row.racks.exists():
        return JsonResponse({"ok": False, "error": "Cannot delete aisle with existing racks."})
    row.delete()
    return JsonResponse({"ok": True})

@login_required
@role_required("admin", "manager")
def aisle_list_api(request):
    qs = Aisle.objects.order_by("zone_id", "aisle_number")
    zid = request.GET.get("zone_id")
    if zid:
        qs = qs.filter(zone_id=zid)
    return JsonResponse({"rows": [aisle_serializer(x) for x in qs]})


@login_required
@role_required("admin", "manager")
def zone_list_api(request):
    qs = Zone.objects.order_by("warehouse_id", "code")
    wid = request.GET.get("warehouse_id")
    if wid:
        qs = qs.filter(warehouse_id=wid)
    return JsonResponse({"rows": [zone_serializer(x) for x in qs]})


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def rack_create_api(request):
    aisle = get_object_or_404(Aisle, pk=request.POST.get("aisle_id"))
    total_levels = max(1, int(request.POST.get("total_levels") or 1))
    rack = Rack.objects.create(
        aisle=aisle,
        rack_number=(request.POST.get("rack_number") or "").strip().upper(),
        total_levels=total_levels,
        levels=total_levels,
    )
    for i in range(1, total_levels + 1):
        Level.objects.get_or_create(rack=rack, level_number=i)
    return JsonResponse({"ok": True, "data": rack_serializer(rack)})


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def rack_update_api(request, rack_id: int):
    row = get_object_or_404(Rack, pk=rack_id)
    row.rack_number = (request.POST.get("rack_number") or row.rack_number).strip().upper()
    row.total_levels = max(1, int(request.POST.get("total_levels") or row.total_levels))
    row.levels = row.total_levels
    row.save(update_fields=["rack_number", "total_levels", "levels"])
    for i in range(1, row.total_levels + 1):
        Level.objects.get_or_create(rack=row, level_number=i)
    return JsonResponse({"ok": True, "data": rack_serializer(row)})


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def rack_delete_api(request, rack_id: int):
    row = get_object_or_404(Rack, pk=rack_id)
    if Bin.objects.filter(level__rack=row).exists():
        return JsonResponse({"ok": False, "error": "Cannot delete rack with existing bins."})
    row.delete()
    return JsonResponse({"ok": True})


@login_required
@role_required("admin", "manager")
def rack_list_api(request):
    qs = Rack.objects.order_by("aisle_id", "rack_number")
    aid = request.GET.get("aisle_id")
    if aid:
        qs = qs.filter(aisle_id=aid)
    return JsonResponse({"rows": [rack_serializer(x) for x in qs]})


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def level_create_api(request):
    rack = get_object_or_404(Rack, pk=request.POST.get("rack_id"))
    level_number = max(1, int(request.POST.get("level_number") or 1))
    row, _ = Level.objects.get_or_create(rack=rack, level_number=level_number)
    return JsonResponse({"ok": True, "data": level_serializer(row)})


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def bin_update_api(request, bin_id: int):
    row = get_object_or_404(Bin, pk=bin_id)
    max_capacity = int(request.POST.get("max_capacity") or row.max_capacity)
    
    if max_capacity > 0 and max_capacity < row.current_capacity:
        return JsonResponse({"ok": False, "error": f"Cannot set capacity lower than current usage ({row.current_capacity})."})
        
    row.max_capacity = max_capacity
    row.size = request.POST.get("size") or row.size
    row.save(update_fields=["max_capacity", "size"])
    return JsonResponse({"ok": True, "data": bin_serializer(row)})


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def bin_delete_api(request, bin_id: int):
    row = get_object_or_404(Bin, pk=bin_id)
    if row.current_capacity > 0:
        return JsonResponse({"ok": False, "error": "Cannot delete a bin that contains items."})
    row.delete()
    return JsonResponse({"ok": True})


@login_required
@role_required("admin", "manager")
def level_list_api(request):
    qs = Level.objects.order_by("rack_id", "level_number")
    rid = request.GET.get("rack_id")
    if rid:
        qs = qs.filter(rack_id=rid)
    return JsonResponse({"rows": [level_serializer(x) for x in qs]})


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def bin_generate_api(request):
    size = request.POST.get("size") or Bin.Size.MEDIUM
    bins_per_level = max(1, int(request.POST.get("bins_per_level") or 1))
    max_capacity = max(0, int(request.POST.get("max_capacity") or 0))
    level = get_object_or_404(Level.objects.select_related("rack__aisle__zone__warehouse"), pk=request.POST.get("level_id"))
    created = 0
    for b in range(1, bins_per_level + 1):
        code = f"{level.rack.aisle.zone.warehouse.code}-{level.rack.aisle.zone.code}-A{level.rack.aisle.aisle_number}-R{level.rack.rack_number}-L{level.level_number}-B{b}"
        _, is_new = Bin.objects.get_or_create(
            bin_code=code,
            defaults={
                "rack": level.rack,
                "level": level,
                "level_number": level.level_number,
                "size": size,
                "max_capacity": max_capacity,
            },
        )
        if is_new:
            created += 1
    return JsonResponse({"ok": True, "created": created})


@login_required
@role_required("admin", "manager")
def bin_list_api(request):
    qs = Bin.objects.select_related("level").order_by("bin_code")
    lid = request.GET.get("level_id")
    if lid:
        qs = qs.filter(level_id=lid)
    return JsonResponse({"rows": [bin_serializer(x) for x in qs]})


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def sku_create_api(request):
    code = (request.POST.get("code") or "").strip().upper()
    row = SKU.objects.create(
        sku_code=code,
        barcode=f"BAR-{code}",
        name=(request.POST.get("name") or "").strip(),
        category=(request.POST.get("category") or "").strip(),
        weight=request.POST.get("weight") or 0,
        dimensions=(request.POST.get("dimensions") or "").strip(),
    )
    return JsonResponse({"ok": True, "data": sku_serializer(row)})


@login_required
@role_required("admin", "manager")
def sku_list_api(request):
    return JsonResponse({"rows": [sku_serializer(x) for x in SKU.objects.order_by("sku_code")]})


@login_required
@role_required("admin", "manager")
def inventory_list_api(request):
    qs = Inventory.objects.select_related("sku", "warehouse", "zone", "bin__level__rack__aisle__zone__warehouse").order_by("-id")
    wid = request.GET.get("warehouse")
    zid = request.GET.get("zone")
    sku = request.GET.get("sku")
    category = request.GET.get("category")
    if wid:
        qs = qs.filter(warehouse_id=wid)
    if zid:
        qs = qs.filter(zone_id=zid)
    if sku:
        qs = qs.filter(sku__sku_code__icontains=sku)
    if category:
        qs = qs.filter(sku__category=category)
    return JsonResponse({"rows": [inventory_serializer(x) for x in qs]})


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def stock_adjustment_api(request):
    sku = get_object_or_404(SKU, pk=request.POST.get("sku_id"))
    bin_obj = get_object_or_404(Bin.objects.select_related("level__rack__aisle__zone__warehouse"), pk=request.POST.get("bin_id"))
    inv = Inventory.objects.filter(sku=sku, bin=bin_obj).select_related("warehouse", "zone").first()
    if not inv:
        return JsonResponse({"ok": False, "error": "Inventory cannot be created manually. Use inbound -> put-away."}, status=400)

    qty = max(1, int(request.POST.get("quantity") or 1))
    adjustment_type = request.POST.get("adjustment_type") or StockAdjustment.AdjustmentType.REMOVE
    if adjustment_type == StockAdjustment.AdjustmentType.ADD:
        inv.quantity += qty
        bin_obj.current_capacity += qty
    else:
        inv.quantity = max(0, inv.quantity - qty)
        bin_obj.current_capacity = max(0, bin_obj.current_capacity - qty)
    inv.save(update_fields=["quantity"])
    bin_obj.save(update_fields=["current_capacity"])

    StockAdjustment.objects.create(
        sku=sku,
        bin=bin_obj,
        adjustment_type=adjustment_type,
        quantity=qty,
        reason=(request.POST.get("reason") or "").strip(),
        created_by=request.user,
    )
    return JsonResponse({"ok": True, "data": inventory_serializer(inv)})

@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def stock_transfer_api(request):
    from django.db import transaction
    from .models import StockLedger, StockBin
    
    sku = get_object_or_404(SKU, pk=request.POST.get("sku_id"))
    from_bin = get_object_or_404(Bin.objects.select_related("level__rack__aisle__zone__warehouse"), pk=request.POST.get("from_bin_id"))
    to_bin = get_object_or_404(Bin.objects.select_related("level__rack__aisle__zone__warehouse"), pk=request.POST.get("to_bin_id"))
    qty = max(1, int(request.POST.get("quantity") or 1))
    
    if from_bin.id == to_bin.id:
        return JsonResponse({"ok": False, "error": "Source and destination cannot be the same."}, status=400)
        
    with transaction.atomic():
        from_inv = Inventory.objects.filter(sku=sku, bin=from_bin).first()
        if not from_inv or from_inv.quantity < qty:
            return JsonResponse({"ok": False, "error": f"Not enough stock in {from_bin.bin_code}. Available: {from_inv.quantity if from_inv else 0}."}, status=400)
            
        if to_bin.max_capacity > 0 and (to_bin.current_capacity + qty) > to_bin.max_capacity:
            return JsonResponse({"ok": False, "error": f"Destination bin {to_bin.bin_code} capacity exceeded."}, status=400)
            
        # Deduct from source
        from_inv.quantity -= qty
        if from_inv.quantity == 0:
            from_inv.delete()
        else:
            from_inv.save(update_fields=["quantity"])
            
        from_bin.current_capacity = max(0, from_bin.current_capacity - qty)
        from_bin.save(update_fields=["current_capacity"])
        
        # Add to destination
        to_inv, _ = Inventory.objects.get_or_create(
            sku=sku,
            bin=to_bin,
            defaults={
                "warehouse": to_bin.level.rack.aisle.zone.warehouse,
                "zone": to_bin.level.rack.aisle.zone,
                "quantity": 0,
            }
        )
        to_inv.quantity += qty
        to_inv.status = Inventory.Status.AVAILABLE
        to_inv.save(update_fields=["quantity", "status"])
        
        to_bin.current_capacity += qty
        to_bin.save(update_fields=["current_capacity"])
        
        # Ledger entry
        StockLedger.objects.create(
            sku=sku,
            movement_type=StockLedger.MovementType.TRANSFER,
            quantity=qty,
            from_bin=from_bin,
            to_bin=to_bin,
            reference_id="MANUAL_TRANSFER",
            performed_by=request.user
        )
        
    return JsonResponse({"ok": True})

