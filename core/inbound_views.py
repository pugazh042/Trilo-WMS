"""Inbound / goods receiving workflow."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .decorators import role_required
from .models import (
    Bin,
    GoodsReceipt,
    InboundPutawayTask,
    Inventory,
    Location,
    PurchaseOrder,
    PurchaseOrderItem,
    QCCheck,
    SKU,
    StagingItem,
    User,
)
from .utils import now_date_str


def _suggested_location_for_sku(sku_id: int, suggested_bin: Bin | None = None) -> Location | None:
    if suggested_bin:
        loc, _ = Location.objects.get_or_create(
            code=suggested_bin.bin_code,
            defaults={
                "zone": suggested_bin.level.rack.aisle.zone.code,
                "aisle": suggested_bin.level.rack.aisle.aisle_number,
                "rack": suggested_bin.level.rack.rack_number,
                "bin": suggested_bin.bin_code,
            },
        )
        return loc
    return Location.objects.order_by("code").first()


def _suggested_bin_for_sku(sku: SKU, quantity: int = 0) -> Bin | None:
    qs = Bin.objects.select_related("level__rack__aisle__zone__warehouse").order_by("current_capacity", "bin_code")
    
    sku_category = sku.category
    
    # 1. Exact match on Zone Category
    if sku_category:
        category_bins = qs.filter(level__rack__aisle__zone__category__iexact=sku_category)
        for b in category_bins:
            if b.max_capacity == 0 or (b.max_capacity - b.current_capacity) >= quantity:
                return b

        # 2. Partial match on Zone Category
        partial_bins = qs.filter(level__rack__aisle__zone__category__icontains=sku_category)
        for b in partial_bins:
            if b.max_capacity == 0 or (b.max_capacity - b.current_capacity) >= quantity:
                return b

    # 3. Fallback to zones with NO specific category (general zones)
    general_bins = qs.filter(level__rack__aisle__zone__category="")
    for b in general_bins:
        if b.max_capacity == 0 or (b.max_capacity - b.current_capacity) >= quantity:
            return b

    # 4. Fallback to any available bin
    for b in qs:
        # max_capacity=0 is treated as no configured cap.
        if b.max_capacity == 0 or (b.max_capacity - b.current_capacity) >= quantity:
            return b
    return qs.first()


@login_required
@role_required("admin", "manager")
def inbound_po_list(request):
    qs = PurchaseOrder.objects.all().order_by("-created_at")
    
    st = request.GET.get("status") or ""
    date_start = request.GET.get("date_start") or ""
    date_end = request.GET.get("date_end") or ""
    supplier = request.GET.get("supplier") or ""
    search = request.GET.get("search") or ""

    if st:
        qs = qs.filter(status=st)
    if date_start:
        qs = qs.filter(created_at__date__gte=date_start)
    if date_end:
        qs = qs.filter(created_at__date__lte=date_end)
    if supplier:
        qs = qs.filter(supplier_name__icontains=supplier)
    if search:
        qs = qs.filter(Q(po_number__icontains=search) | Q(supplier_name__icontains=search))

    return render(
        request,
        "inbound/inbound_po_list.html",
        {
            "orders": qs[:200],
            "status_filter": st,
            "date_start": date_start,
            "date_end": date_end,
            "supplier": supplier,
            "search": search,
            "today_str": now_date_str()
        },
    )


@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def create_po(request):
    if request.method == "GET":
        skus = SKU.objects.order_by("sku_code")
        return render(request, "inbound/create_po.html", {"skus": skus, "today_str": now_date_str()})

    supplier = (request.POST.get("supplier_name") or "").strip()
    ed = request.POST.get("expected_delivery_date") or None
    if not supplier:
        messages.error(request, "Supplier is required.")
        return redirect("/inbound/create/")
    sku_ids = request.POST.getlist("sku_id")
    qtys = request.POST.getlist("qty")
    with transaction.atomic():
        po = PurchaseOrder.objects.create(supplier_name=supplier, expected_delivery_date=ed or None)
        for sku_id, qty in zip(sku_ids, qtys):
            try:
                q = int(qty)
            except Exception:
                q = 0
            if not sku_id or q <= 0:
                continue
            sku = SKU.objects.filter(id=int(sku_id)).first()
            if sku:
                PurchaseOrderItem.objects.create(purchase_order=po, sku=sku, expected_qty=q)
    messages.success(request, f"PO {po.po_number} created.")
    return redirect(f"/inbound/{po.id}/")


@login_required
@role_required("admin", "manager")
def inbound_dock_arrival_list(request):
    # POs that are created and waiting for arrival, or already arrived but not fully received
    qs = PurchaseOrder.objects.filter(status__in=[PurchaseOrder.Status.CREATED, PurchaseOrder.Status.ARRIVED, PurchaseOrder.Status.RECEIVING]).order_by("-created_at")
    return render(request, "inbound/inbound_dock_arrival_list.html", {
        "orders": qs,
        "today_str": now_date_str()
    })

@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def dock_arrival(request, po_id: int):
    po = get_object_or_404(PurchaseOrder, pk=po_id)
    if request.method == "POST":
        po.dock_number = (request.POST.get("dock_number") or "").strip()
        if po.status == PurchaseOrder.Status.CREATED:
            po.status = PurchaseOrder.Status.ARRIVED
            po.save(update_fields=["dock_number", "status"])
        else:
            po.save(update_fields=["dock_number"])
        messages.success(request, "Dock recorded. PO marked arrived.")
        return redirect(f"/inbound/{po.id}/dock/")
    return render(request, "inbound/dock_arrival.html", {"po": po, "today_str": now_date_str()})


@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def goods_receipt_view(request, po_id: int):
    po = get_object_or_404(PurchaseOrder.objects.prefetch_related("lines__sku"), pk=po_id)
    if po.status not in (PurchaseOrder.Status.ARRIVED, PurchaseOrder.Status.RECEIVING):
        messages.error(request, "PO must be arrived before receiving.")
        return redirect(f"/inbound/{po.id}/")

    gr = GoodsReceipt.objects.filter(purchase_order=po, status=GoodsReceipt.Status.IN_PROGRESS).first()
    if not gr:
        gr = GoodsReceipt.objects.create(
            purchase_order=po,
            received_by=request.user,
            status=GoodsReceipt.Status.IN_PROGRESS,
        )
        po.status = PurchaseOrder.Status.RECEIVING
        po.save(update_fields=["status"])

    if request.method == "POST":
        for line in po.lines.all():
            raw = request.POST.get(f"recv_{line.id}") or "0"
            batch = (request.POST.get(f"batch_{line.id}") or "").strip()
            try:
                v = int(raw)
            except Exception:
                v = 0
            if v < 0:
                v = 0
            if v > line.expected_qty:
                messages.error(request, f"Cannot receive more than expected for {line.sku.sku_code}.")
                return redirect(f"/inbound/{po.id}/receipt/")
            line.received_qty = v
            line.received_batch = batch
            line.save(update_fields=["received_qty", "received_batch"])

        gr.received_at = timezone.now()
        gr.status = GoodsReceipt.Status.COMPLETED
        gr.save(update_fields=["received_at", "status"])
        po.status = PurchaseOrder.Status.QC
        po.save(update_fields=["status"])
        messages.success(request, "Receipt saved. Proceed to QC.")
        return redirect(f"/inbound/{po.id}/qc/")

    return render(request, "inbound/goods_receipt.html", {"po": po, "gr": gr, "today_str": now_date_str()})


@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def barcode_scan_view(request, po_id: int):
    po = get_object_or_404(PurchaseOrder.objects.prefetch_related("lines__sku"), pk=po_id)
    gr = GoodsReceipt.objects.filter(purchase_order=po, status=GoodsReceipt.Status.IN_PROGRESS).first()
    if not gr:
        messages.error(request, "Open goods receipt first.")
        return redirect(f"/inbound/{po.id}/receipt/")

    scan_result = None
    if request.method == "POST":
        code = (request.POST.get("sku_code") or "").strip().upper()
        batch = (request.POST.get("batch") or "").strip()
        qty = max(1, int(request.POST.get("qty") or 1))
        line = po.lines.filter(sku__sku_code__iexact=code).first()
        if not line:
            messages.error(request, "SKU not on this PO.")
        elif line.received_qty >= line.expected_qty:
            messages.warning(request, "Already at expected quantity.")
        else:
            incoming = min(qty, max(0, line.expected_qty - line.received_qty))
            line.received_qty += incoming
            if batch:
                line.received_batch = batch
            line.save(update_fields=["received_qty", "received_batch"])
            scan_result = {"sku": line.sku, "count": line.received_qty, "expected": line.expected_qty}

    return render(
        request,
        "inbound/barcode_scan.html",
        {"po": po, "gr": gr, "scan_result": scan_result, "today_str": now_date_str()},
    )


@login_required
@role_required("admin", "manager")
def inbound_qc_list(request):
    # POs that are arrived/receiving and have completed goods receipts waiting for QC
    qs = PurchaseOrder.objects.filter(status=PurchaseOrder.Status.QC).order_by("-created_at")
    return render(request, "inbound/inbound_qc_list.html", {
        "orders": qs,
        "today_str": now_date_str()
    })


@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def qc_screen(request, po_id: int):
    po = get_object_or_404(PurchaseOrder.objects.prefetch_related("lines__sku"), pk=po_id)
    gr = GoodsReceipt.objects.filter(purchase_order=po, status=GoodsReceipt.Status.COMPLETED).order_by("-id").first()
    if not gr:
        messages.error(request, "Complete goods receipt first.")
        return redirect(f"/inbound/{po.id}/receipt/")

    for line in po.lines.all():
        if line.received_qty <= 0:
            continue
        QCCheck.objects.get_or_create(
            goods_receipt=gr,
            sku=line.sku,
            defaults={
                "received_qty": line.received_qty,
                "passed_qty": line.received_qty,
                "failed_qty": 0,
                "status": QCCheck.Status.PENDING,
            },
        )

    if request.method == "POST":
        with transaction.atomic():
            for qc in QCCheck.objects.filter(goods_receipt=gr).select_for_update():
                try:
                    p = int(request.POST.get(f"pass_{qc.id}") or 0)
                    f = int(request.POST.get(f"fail_{qc.id}") or 0)
                except Exception:
                    p, f = 0, 0
                if p + f != qc.received_qty:
                    messages.error(
                        request,
                        f"Passed + failed must equal received for {qc.sku.sku_code}.",
                    )
                    checks = list(QCCheck.objects.filter(goods_receipt=gr).select_related("sku"))
                    return render(
                        request,
                        "inbound/qc_screen.html",
                        {"po": po, "gr": gr, "checks": checks, "today_str": now_date_str()},
                    )
                qc.passed_qty = p
                qc.failed_qty = f
                if f > 0 and p == 0:
                    qc.status = QCCheck.Status.REJECTED
                elif p > 0:
                    qc.status = QCCheck.Status.APPROVED
                else:
                    qc.status = QCCheck.Status.REJECTED
                qc.save(update_fields=["passed_qty", "failed_qty", "status"])

            StagingItem.objects.filter(purchase_order=po).delete()
            zone = (request.POST.get("staging_zone") or "STG-A").strip() or "STG-A"
            for qc in QCCheck.objects.filter(goods_receipt=gr):
                if qc.passed_qty > 0 and qc.status == QCCheck.Status.APPROVED:
                    StagingItem.objects.create(
                        sku=qc.sku,
                        quantity=qc.passed_qty,
                        staging_zone=zone,
                        status=StagingItem.Status.READY_FOR_PUTAWAY,
                        purchase_order=po,
                    )

        messages.success(request, "QC approved. Items moved to staging.")
        return redirect(f"/inbound/{po.id}/staging/")

    checks = list(QCCheck.objects.filter(goods_receipt=gr).select_related("sku"))
    return render(
        request,
        "inbound/qc_screen.html",
        {"po": po, "gr": gr, "checks": checks, "today_str": now_date_str()},
    )


@login_required
@role_required("admin", "manager")
def staging_area(request, po_id: int):
    po = get_object_or_404(PurchaseOrder, pk=po_id)
    staging = list(StagingItem.objects.filter(purchase_order=po).select_related("sku"))
    return render(
        request,
        "inbound/staging_area.html",
        {"po": po, "staging": staging, "today_str": now_date_str()},
    )


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def create_putaway_tasks(request, po_id: int):
    po = get_object_or_404(PurchaseOrder, pk=po_id)
    workers = list(User.objects.filter(role=User.Role.PUTAWAY, is_active=True))
    n = 0
    with transaction.atomic():
        InboundPutawayTask.objects.filter(purchase_order=po).exclude(status=InboundPutawayTask.Status.DONE).delete()
        idx = 0
        for st in StagingItem.objects.filter(
            purchase_order=po,
            status=StagingItem.Status.READY_FOR_PUTAWAY,
        ):
            suggested_bin = _suggested_bin_for_sku(st.sku, st.quantity)
            loc = _suggested_location_for_sku(st.sku_id, suggested_bin)
            assign = workers[idx % len(workers)] if workers else None
            idx += 1
            InboundPutawayTask.objects.create(
                purchase_order=po,
                sku=st.sku,
                quantity=st.quantity,
                suggested_location=loc,
                suggested_bin=suggested_bin,
                assigned_to=assign,
                status=InboundPutawayTask.Status.OPEN,
            )
            st.status = StagingItem.Status.CLEARED
            st.save(update_fields=["status"])
            n += 1
        if n:
            po.status = PurchaseOrder.Status.COMPLETED
            po.save(update_fields=["status"])
    messages.success(request, f"Created {n} put-away task(s). PO completed.")
    return redirect(f"/inbound/{po.id}/staging/")


@login_required
@role_required("admin", "manager", "putaway")
def inbound_putaway_list(request):
    user_role = (getattr(request.user, "role", "") or "").lower()
    tasks_qs = InboundPutawayTask.objects.exclude(status=InboundPutawayTask.Status.DONE)
    if user_role == User.Role.PUTAWAY:
        tasks_qs = tasks_qs.filter(assigned_to=request.user)
    tasks = tasks_qs.select_related(
        "assigned_to",
        "sku",
        "suggested_location",
        "suggested_bin",
        "purchase_order",
    )
    bins = Bin.objects.select_related("level__rack__aisle__zone__warehouse").order_by("bin_code")
    return render(
        request,
        "inbound/inbound_putaway_list.html",
        {
            "tasks": tasks,
            "bins": bins,
            "can_confirm_putaway": user_role == User.Role.PUTAWAY,
            "today_str": now_date_str(),
        },
    )


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def inbound_putaway_set_suggested_bin(request, task_id: int):
    task = get_object_or_404(
        InboundPutawayTask.objects.select_related("purchase_order"),
        pk=task_id,
        status=InboundPutawayTask.Status.OPEN,
    )
    bin_id = request.POST.get("suggested_bin_id")
    if not bin_id:
        messages.error(request, "Please select a suggested bin.")
        return redirect("/inbound/putaway/tasks/")
    suggested_bin = Bin.objects.filter(pk=bin_id).first()
    if not suggested_bin:
        messages.error(request, "Selected bin not found.")
        return redirect("/inbound/putaway/tasks/")
    task.suggested_bin = suggested_bin
    task.save(update_fields=["suggested_bin"])
    messages.success(request, f"Suggested bin updated for {task.sku.sku_code}.")
    return redirect("/inbound/putaway/tasks/")


@login_required
@role_required("putaway")
@require_http_methods(["GET", "POST"])
def inbound_putaway_complete(request, task_id: int):
    task = get_object_or_404(InboundPutawayTask, pk=task_id, assigned_to=request.user)
    
    if request.method == "GET":
        if task.status == InboundPutawayTask.Status.OPEN:
            task.status = InboundPutawayTask.Status.IN_PROGRESS
            task.save(update_fields=["status"])
        return render(request, "inbound/inbound_putaway_detail.html", {"task": task, "today_str": now_date_str()})

    from .models import StockBin

    bin_code = (request.POST.get("bin_code") or "").strip()
    sku_code = (request.POST.get("sku_code") or "").strip()

    if not task.suggested_bin:
        messages.error(request, "No suggested bin available.")
        return redirect(f"/inbound/putaway/tasks/{task.id}/complete/")

    if bin_code.upper() != task.suggested_bin.bin_code.upper():
        messages.error(request, f"Wrong bin. Expected {task.suggested_bin.bin_code}.")
        return redirect(f"/inbound/putaway/tasks/{task.id}/complete/")

    if sku_code.upper() != task.sku.sku_code.upper():
        messages.error(request, f"Wrong SKU. Expected {task.sku.sku_code}.")
        return redirect(f"/inbound/putaway/tasks/{task.id}/complete/")

    with transaction.atomic():
        target_bin = task.suggested_bin
        if target_bin.max_capacity > 0 and (target_bin.current_capacity + task.quantity) > target_bin.max_capacity:
            messages.error(request, f"Cannot put-away {task.quantity} items into {target_bin.bin_code}. Capacity exceeded.")
            return redirect(f"/inbound/putaway/tasks/{task.id}/complete/")
            
        loc = task.suggested_location
        if not loc:
            loc, _ = Location.objects.get_or_create(
                code=target_bin.bin_code,
                defaults={
                    "zone": target_bin.level.rack.aisle.zone.code,
                    "aisle": target_bin.level.rack.aisle.aisle_number,
                    "rack": target_bin.level.rack.rack_number,
                    "bin": target_bin.bin_code,
                },
            )
        sb, _ = StockBin.objects.select_for_update().get_or_create(
            sku=task.sku,
            location=loc,
            defaults={"on_hand": 0, "reserved": 0},
        )
        sb.on_hand += task.quantity
        sb.save(update_fields=["on_hand"])
        sku = task.sku
        sku.quantity += task.quantity
        sku.save(update_fields=["quantity"])
        inv, _ = Inventory.objects.get_or_create(
            sku=task.sku,
            bin=target_bin,
            defaults={
                "warehouse": target_bin.level.rack.aisle.zone.warehouse,
                "zone": target_bin.level.rack.aisle.zone,
                "quantity": 0,
            },
        )
        inv.quantity += task.quantity
        inv.status = Inventory.Status.AVAILABLE
        inv.save(update_fields=["quantity", "status"])
        target_bin.current_capacity += task.quantity
        target_bin.save(update_fields=["current_capacity"])
        task.status = InboundPutawayTask.Status.DONE
        task.save(update_fields=["status"])

        from .models import StockLedger
        StockLedger.objects.create(
            sku=task.sku,
            movement_type=StockLedger.MovementType.PUTAWAY,
            quantity=task.quantity,
            to_bin=target_bin,
            reference_id=f"PUTAWAY-{task.id}",
            performed_by=request.user
        )
    messages.success(request, "Put-away confirmed. Stock updated.")
    return redirect("/inbound/putaway/tasks/")
