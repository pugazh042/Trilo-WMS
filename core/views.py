from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .decorators import role_required
from .models import (
    Bin,
    Consolidation,
    InboundPutawayTask,
    Inventory,
    Level,
    Order,
    OrderItem,
    Packing,
    PurchaseOrder,
    PickTask,
    Rack,
    Return,
    Shipment,
    SKU,
    StockAdjustment,
    StockBin,
    User,
    Warehouse,
    Zone,
)
from .utils import (
    can_resend_otp,
    clear_otp_session,
    create_otp_session,
    now_date_str,
    resend_otp_session,
    role_home_url,
    verify_otp_session,
)


def _unreserve_incomplete_pick_tasks(order: Order) -> None:
    for pt in PickTask.objects.filter(order=order).exclude(status=PickTask.Status.COMPLETED):
        sb = StockBin.objects.select_for_update().get(pk=pt.stock_bin_id)
        sb.reserved = max(0, sb.reserved - pt.quantity)
        sb.save(update_fields=["reserved"])
        pt.delete()


def _sku_stock_available(sku_id: int) -> int:
    total = 0
    for sb in StockBin.objects.filter(sku_id=sku_id):
        total += sb.available
    return total


def _allocate_order_core(order: Order) -> tuple[int, bool]:
    pickers = list(
        User.objects.filter(role=User.Role.PICKER, is_active=True).annotate(open_cnt=Count("pick_tasks"))
    )
    if not pickers:
        return 0, False
    pickers.sort(key=lambda u: u.open_cnt)

    created = 0
    fully = True
    with transaction.atomic():
        _unreserve_incomplete_pick_tasks(order)
        for item in order.items.all():
            OrderItem.objects.filter(pk=item.pk).update(allocated_quantity=0)

        picker_idx = 0
        for item in order.items.select_for_update().all():
            remaining = int(item.quantity)
            bins = (
                StockBin.objects.select_for_update()
                .filter(sku=item.sku)
                .order_by("-on_hand")
            )
            allocated_line = 0
            for sb in bins:
                if remaining <= 0:
                    break
                avail = sb.available
                if avail <= 0:
                    continue
                take = min(avail, remaining)
                sb.reserved += take
                sb.save(update_fields=["reserved"])
                assigned = pickers[picker_idx % len(pickers)]
                picker_idx += 1
                PickTask.objects.create(
                    order=order,
                    order_item=item,
                    sku=item.sku,
                    stock_bin=sb,
                    source_location=sb.location.code,
                    picker=assigned,
                    quantity=take,
                )
                allocated_line += take
                created += 1
                remaining -= take
            OrderItem.objects.filter(pk=item.pk).update(allocated_quantity=allocated_line)
            if remaining > 0:
                fully = False

        order.allocation_status = (
            Order.AllocationStatus.FULLY_ALLOCATED
            if fully and created
            else Order.AllocationStatus.PARTIALLY_ALLOCATED
        )
        if created == 0:
            order.allocation_status = Order.AllocationStatus.NOT_ALLOCATED
        else:
            order.status = Order.Status.ALLOCATED
        order.save(update_fields=["allocation_status", "status"])
    return created, fully


def _maybe_start_consolidation(order: Order) -> None:
    if not PickTask.objects.filter(order=order).exists():
        return
    if PickTask.objects.filter(order=order).exclude(status=PickTask.Status.COMPLETED).exists():
        return
    order.status = Order.Status.CONSOLIDATING
    order.save(update_fields=["status"])
    Consolidation.objects.get_or_create(
        order=order, defaults={"status": Consolidation.Status.PENDING}
    )


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect(role_home_url(getattr(request.user, "role", "")))

    if request.method == "GET":
        return render(request, "auth/login.html")

    identifier = (request.POST.get("identifier") or "").strip()
    password = request.POST.get("password") or ""
    remember_me = bool(request.POST.get("remember_me"))

    if not identifier or not password:
        messages.error(request, "Please enter your login and password.")
        return render(request, "auth/login.html", {"identifier": identifier})

    user = None
    if "@" in identifier:
        user = User.objects.filter(email__iexact=identifier).first()
    if not user:
        user = User.objects.filter(employee_id__iexact=identifier).first()

    if not user or not user.check_password(password):
        messages.error(request, "Invalid login credentials.")
        return render(request, "auth/login.html", {"identifier": identifier})

    if not user.is_active:
        messages.error(request, "Your account is disabled. Contact admin.")
        return render(request, "auth/login.html", {"identifier": identifier})

    otp = create_otp_session(request=request, user_id=user.id, remember_me=remember_me)
    request.session["otp_mask"] = identifier
    messages.info(request, f"Your OTP is {otp} (demo). It expires in 2 minutes.")
    return redirect("/otp/")


@require_http_methods(["GET", "POST"])
def otp_view(request):
    blob = request.session.get("otp") or {}
    masked = request.session.get("otp_mask") or ""
    if request.user.is_authenticated:
        return redirect(role_home_url(getattr(request.user, "role", "")))

    if not blob:
        messages.error(request, "Please login to continue.")
        return redirect("/")

    if request.method == "GET":
        return render(
            request,
            "auth/otp_verify.html",
            {
                "masked": masked,
                "resend_remaining": can_resend_otp(request=request)[1],
            },
        )

    otp_code = request.POST.get("otp_code") or ""
    result = verify_otp_session(request=request, otp_code=otp_code)
    if not result.ok or not result.user_id:
        messages.error(request, result.error or "OTP verification failed.")
        return render(
            request,
            "auth/otp_verify.html",
            {
                "masked": masked,
                "resend_remaining": can_resend_otp(request=request)[1],
            },
        )

    user = User.objects.filter(id=result.user_id).first()
    if not user:
        clear_otp_session(request=request)
        messages.error(request, "Login session invalid. Please login again.")
        return redirect("/")

    auth_login(request, user)
    clear_otp_session(request=request)
    request.session.pop("otp_mask", None)

    if result.remember_me:
        request.session.set_expiry(60 * 60 * 24 * 30)
    else:
        request.session.set_expiry(0)

    return redirect(role_home_url(getattr(user, "role", "")))


@require_http_methods(["POST"])
def resend_otp(request):
    ok, remaining = can_resend_otp(request=request)
    if not ok:
        return JsonResponse({"ok": False, "error": "Cooldown", "remaining": remaining}, status=429)

    otp = resend_otp_session(request=request)
    if not otp:
        return JsonResponse({"ok": False, "error": "No OTP session"}, status=400)

    return JsonResponse({"ok": True, "otp": otp, "expires_in": 120})


@login_required
def dashboard(request):
    role = (getattr(request.user, "role", "") or "").lower()
    
    if role == "picker":
        active_picks = PickTask.objects.filter(picker=request.user, status__in=[PickTask.Status.PENDING, PickTask.Status.IN_PROGRESS])
        completed_picks = PickTask.objects.filter(picker=request.user, status=PickTask.Status.COMPLETED).count()
        return render(request, "dashboard/picker.html", {"active_tasks": active_picks, "completed_count": completed_picks, "today_str": now_date_str()})
        
    elif role == "packer":
        pending_packs = Packing.objects.filter(status__in=[Packing.Status.PENDING, Packing.Status.IN_PROGRESS])
        completed_packs = Packing.objects.filter(packed_by=request.user, status=Packing.Status.COMPLETED).count()
        return render(request, "dashboard/packer.html", {"active_tasks": pending_packs, "completed_count": completed_packs, "today_str": now_date_str()})
        
    elif role == "putaway":
        open_putaways = InboundPutawayTask.objects.filter(assigned_to=request.user).exclude(status=InboundPutawayTask.Status.DONE)
        completed_putaways = InboundPutawayTask.objects.filter(assigned_to=request.user, status=InboundPutawayTask.Status.DONE).count()
        return render(request, "dashboard/putaway.html", {"active_tasks": open_putaways, "completed_count": completed_putaways, "today_str": now_date_str()})
        
    if role not in ("admin", "manager"):
        messages.error(request, "You do not have a valid role.")
        return redirect("/")

    total_orders = Order.objects.count()
    completed_orders = Order.objects.filter(status=Order.Status.COMPLETED).count()
    inventory_count = Inventory.objects.count()
    low_stock = SKU.objects.filter(quantity__lt=10).count()
    active_tasks = PickTask.objects.filter(
        status__in=[PickTask.Status.PENDING, PickTask.Status.IN_PROGRESS]
    ).count()
    picks_done = PickTask.objects.filter(status=PickTask.Status.COMPLETED).count()
    worker_perf = []
    for u in User.objects.filter(role=User.Role.PICKER):
        done = PickTask.objects.filter(picker=u, status=PickTask.Status.COMPLETED).count()
        worker_perf.append({"name": u.get_full_name() or u.username, "picks": done})
    module_tasks = {
        "inbound": PurchaseOrder.objects.filter(
            status__in=[PurchaseOrder.Status.ARRIVED, PurchaseOrder.Status.RECEIVING, PurchaseOrder.Status.QC]
        ).count(),
        "picking": PickTask.objects.exclude(status=PickTask.Status.COMPLETED).count(),
        "packing": Packing.objects.exclude(status=Packing.Status.COMPLETED).count(),
        "shipping": Shipment.objects.exclude(status=Shipment.Status.DELIVERED).count(),
    }
    order_status = {
        "pending": Order.objects.filter(status=Order.Status.PENDING).count(),
        "allocated": Order.objects.filter(status=Order.Status.ALLOCATED).count(),
        "packing": Order.objects.filter(status=Order.Status.PACKING).count(),
        "completed": Order.objects.filter(status=Order.Status.COMPLETED).count(),
    }
    orders_over_time = []
    
    graph_start = request.GET.get("graph_start")
    graph_end = request.GET.get("graph_end")
    graph_days = request.GET.get("graph_days", "7")
    
    if graph_start and graph_end:
        try:
            start_dt = timezone.datetime.strptime(graph_start, "%Y-%m-%d").date()
            end_dt = timezone.datetime.strptime(graph_end, "%Y-%m-%d").date()
            if start_dt <= end_dt:
                delta = (end_dt - start_dt).days
                for i in range(delta, -1, -1):
                    d = end_dt - timezone.timedelta(days=i)
                    cnt = Order.objects.filter(created_at__date=d).count()
                    orders_over_time.append({"date": d.strftime("%d %b"), "count": cnt})
        except ValueError:
            pass
            
    if not orders_over_time:
        try:
            days = int(graph_days)
        except Exception:
            days = 7
        for i in range(days - 1, -1, -1):
            d = timezone.localdate() - timezone.timedelta(days=i)
            cnt = Order.objects.filter(created_at__date=d).count()
            orders_over_time.append({"date": d.strftime("%d %b"), "count": cnt})

    low_stock_items = list(SKU.objects.filter(quantity__lt=10).values("sku_code", "name", "quantity")[:10])

    return render(
        request,
        "dashboard/dashboard.html",
        {
            "total_orders": total_orders,
            "completed_orders": completed_orders,
            "inventory_count": inventory_count,
            "low_stock": low_stock,
            "active_tasks": active_tasks,
            "picks_done": picks_done,
            "worker_perf_json": json.dumps(worker_perf),
            "module_tasks_json": json.dumps(module_tasks),
            "order_status_json": json.dumps(order_status),
            "orders_over_time_json": json.dumps(orders_over_time),
            "graph_days": graph_days,
            "graph_start": graph_start or "",
            "graph_end": graph_end or "",
            "low_stock_items": low_stock_items,
            "active_workers": User.objects.filter(is_active=True).exclude(role=User.Role.ADMIN).count(),
            "today_str": now_date_str(),
        },
    )


@login_required
def dashboard_api(request):
    role = (getattr(request.user, "role", "") or "").lower()
    if role in ("picker", "packer", "putaway"):
        return JsonResponse(
            {
                "total_orders": 0,
                "completed_orders": 0,
                "pending_orders": 0,
                "total_stock": 0,
                "low_stock_items": [],
                "worker_performance": [],
            }
        )

    pending_orders = Order.objects.exclude(status=Order.Status.COMPLETED).count()
    completed_orders = Order.objects.filter(status=Order.Status.COMPLETED).count()
    return JsonResponse(
        {
            "total_orders": Order.objects.count(),
            "completed_orders": completed_orders,
            "pending_orders": pending_orders,
            "total_stock": int(sum([x.quantity for x in SKU.objects.all()])),
            "low_stock_items": list(SKU.objects.filter(quantity__lt=10).values("sku_code", "name", "quantity")[:20]),
            "worker_performance": [
                {
                    "worker": u.get_full_name() or u.username,
                    "completed_picks": PickTask.objects.filter(
                        picker=u, status=PickTask.Status.COMPLETED
                    ).count(),
                }
                for u in User.objects.filter(role=User.Role.PICKER, is_active=True)
            ],
        }
    )


@login_required
@require_http_methods(["GET", "POST"])
def profile(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "change_password":
            current_pw = request.POST.get("current_password")
            new_pw = request.POST.get("new_password")
            confirm_pw = request.POST.get("confirm_password")
            
            if not request.user.check_password(current_pw):
                messages.error(request, "Current password is incorrect.")
            elif new_pw != confirm_pw:
                messages.error(request, "New passwords do not match.")
            else:
                try:
                    validate_password(new_pw, request.user)
                    request.user.set_password(new_pw)
                    request.user.save()
                    update_session_auth_hash(request, request.user)
                    messages.success(request, "Password changed successfully.")
                except ValidationError as e:
                    for error in e.messages:
                        messages.error(request, error)
        elif action == "update_profile":
            if "profile_photo" in request.FILES:
                request.user.profile_photo = request.FILES["profile_photo"]
                request.user.save()
                messages.success(request, "Profile photo updated.")
        return redirect("/profile/")
        
    return render(request, "profile/profile.html", {"today_str": now_date_str()})


@require_http_methods(["GET"])
def logout_view(request):
    auth_logout(request)
    messages.info(request, "Logged out successfully.")
    return redirect("/")


@login_required
@role_required("admin", "manager")
def orders_list(request):
    qs = Order.objects.annotate(items_count=Count("items"))

    st = request.GET.get("status") or ""
    date_start = request.GET.get("date_start") or ""
    date_end = request.GET.get("date_end") or ""
    search = request.GET.get("search") or ""
    sort_by = request.GET.get("sort_by") or "latest"

    if st:
        qs = qs.filter(status=st)
    if date_start:
        qs = qs.filter(created_at__date__gte=date_start)
    if date_end:
        qs = qs.filter(created_at__date__lte=date_end)
    if search:
        qs = qs.filter(Q(order_id__icontains=search) | Q(customer_name__icontains=search))

    if sort_by == "oldest":
        qs = qs.order_by("created_at")
    else:
        qs = qs.order_by("-created_at")

    return render(
        request, 
        "orders/orders_list.html", 
        {
            "orders": qs[:200], 
            "status_filter": st,
            "date_start": date_start,
            "date_end": date_end,
            "search": search,
            "sort_by": sort_by,
            "today_str": now_date_str()
        }
    )


@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def order_create(request):
    if request.method == "GET":
        skus = SKU.objects.order_by("sku_code")
        return render(request, "orders/create_order.html", {"skus": skus, "today_str": now_date_str()})

    customer_name = (request.POST.get("customer_name") or "").strip()
    customer_phone = (request.POST.get("customer_phone") or "").strip()
    delivery_address = (request.POST.get("delivery_address") or "").strip()
    if not customer_name:
        messages.error(request, "Customer name is required.")
        return redirect("/orders/create/")

    sku_ids = request.POST.getlist("sku_id")
    qtys = request.POST.getlist("qty")
    if not sku_ids:
        messages.error(request, "Add at least one SKU.")
        return redirect("/orders/create/")

    with transaction.atomic():
        order = Order.objects.create(
            customer_name=customer_name,
            customer_phone=customer_phone,
            delivery_address=delivery_address,
            status=Order.Status.PENDING,
        )
        for sku_id, qty in zip(sku_ids, qtys):
            try:
                q = int(qty)
            except Exception:
                q = 0
            if not sku_id or q <= 0:
                continue
            sku = SKU.objects.filter(id=int(sku_id)).first()
            if not sku:
                continue
            OrderItem.objects.create(order=order, sku=sku, quantity=q)

    messages.success(request, f"Order {order.order_id} created.")
    return redirect(f"/orders/{order.id}/")


@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def order_update(request, order_id: int):
    order = get_object_or_404(Order, pk=order_id)
    if request.method == "GET":
        skus = SKU.objects.order_by("sku_code")
        return render(
            request,
            "orders/order_update.html",
            {"order": order, "skus": skus, "today_str": now_date_str()},
        )

    order.customer_name = (request.POST.get("customer_name") or "").strip()
    order.customer_phone = (request.POST.get("customer_phone") or "").strip()
    order.delivery_address = (request.POST.get("delivery_address") or "").strip()
    order.save(update_fields=["customer_name", "customer_phone", "delivery_address"])
    messages.success(request, "Order updated.")
    return redirect(f"/orders/{order.id}/")


@login_required
@role_required("admin", "manager")
def order_detail(request, order_id: int):
    order = Order.objects.filter(pk=order_id).prefetch_related("items__sku", "pick_tasks__picker", "pick_tasks__sku").first()
    if not order:
        messages.error(request, "Order not found.")
        return redirect("/orders/")
    return render(request, "orders/order_detail.html", {"order": order, "today_str": now_date_str()})


@login_required
@role_required("admin", "manager")
def allocation_page(request):
    orders = Order.objects.annotate(items_count=Count("items")).order_by("-created_at")[:100]
    selected_id = request.GET.get("order_id")
    selected = None
    line_rows = []
    if selected_id:
        selected = Order.objects.filter(pk=selected_id).prefetch_related("items__sku", "pick_tasks").first()
        if selected:
            for it in selected.items.all():
                avail = _sku_stock_available(it.sku_id)
                line_rows.append(
                    {
                        "item": it,
                        "available": avail,
                        "ok": avail >= it.quantity,
                    }
                )
    return render(
        request,
        "orders/allocation_page.html",
        {
            "orders": orders,
            "selected": selected,
            "line_rows": line_rows,
            "today_str": now_date_str(),
        },
    )


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def allocate_order(request, order_id: int):
    order = Order.objects.filter(pk=order_id).first()
    if not order:
        messages.error(request, "Order not found.")
        return redirect("/orders/")
    if order.on_hold:
        messages.error(request, "Order is on hold.")
        return redirect(f"/allocation/?order_id={order_id}")

    created, _ = _allocate_order_core(order)
    if created:
        messages.success(request, f"Allocation complete. Created {created} pick task(s).")
    else:
        messages.error(request, "Allocation failed. No stock available.")
    next_url = request.POST.get("next") or f"/orders/{order_id}/"
    return redirect(next_url)


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def auto_allocate(request, order_id: int):
    return allocate_order(request, order_id)


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def hold_order(request, order_id: int):
    order = get_object_or_404(Order, pk=order_id)
    order.on_hold = not order.on_hold
    order.save(update_fields=["on_hold"])
    messages.info(request, "Hold toggled." if order.on_hold else "Hold released.")
    return redirect(request.POST.get("next") or "/allocation/")


@login_required
@role_required("picker")
def pick_task_list(request):
    tasks = (
        PickTask.objects.filter(picker=request.user)
        .select_related("order", "sku", "stock_bin__location")
        .order_by("status", "created_at")
    )
    return render(request, "picking/pick_task_list.html", {"tasks": tasks, "today_str": now_date_str()})


@login_required
@role_required("picker")
@require_http_methods(["GET", "POST"])
def pick_task_detail(request, task_id: int):
    task = (
        PickTask.objects.select_related("order", "sku", "stock_bin__location")
        .filter(id=task_id, picker=request.user)
        .first()
    )
    if not task:
        messages.error(request, "Pick task not found.")
        return redirect("/picking/")

    if request.method == "GET":
        if task.status == PickTask.Status.PENDING:
            task.status = PickTask.Status.IN_PROGRESS
            task.save(update_fields=["status"])
            task.order.status = Order.Status.PICKING
            task.order.save(update_fields=["status"])
        return render(request, "picking/pick_task_detail.html", {"task": task, "today_str": now_date_str()})

    action = request.POST.get("action") or "complete"
    if action == "issue":
        return report_issue(request, task)

    return complete_pick_task(request, task)


def complete_pick_task(request, task: PickTask):
    bin_code = (request.POST.get("bin_code") or "").strip()
    sku_code = (request.POST.get("sku_code") or "").strip()
    qty_str = (request.POST.get("qty") or "").strip()
    try:
        qty = int(qty_str)
    except Exception:
        qty = 0

    expected_bin = task.stock_bin.location.code
    expected_sku = task.sku.sku_code

    if bin_code != expected_bin:
        messages.error(request, f"Wrong bin. Expected {expected_bin}.")
        return redirect(f"/picking/task/{task.id}/")

    if sku_code.upper() != expected_sku.upper():
        messages.error(request, f"Wrong SKU. Expected {expected_sku}.")
        return redirect(f"/picking/task/{task.id}/")

    if qty <= 0 or qty > task.quantity:
        messages.error(request, f"Invalid quantity. Must be 1..{task.quantity}.")
        return redirect(f"/picking/task/{task.id}/")

    with transaction.atomic():
        sb = StockBin.objects.select_for_update().get(id=task.stock_bin_id)
        sb.on_hand = max(0, sb.on_hand - qty)
        sb.reserved = max(0, sb.reserved - qty)
        sb.save(update_fields=["on_hand", "reserved"])

        sku = SKU.objects.select_for_update().get(pk=task.sku_id)
        sku.quantity = max(0, sku.quantity - qty)
        sku.save(update_fields=["quantity"])

        task.picked_qty = qty
        task.status = PickTask.Status.COMPLETED
        task.save(update_fields=["picked_qty", "status"])

    _maybe_start_consolidation(task.order)
    messages.success(request, "Pick confirmed.")
    return redirect("/picking/")


def report_issue(request, task: PickTask):
    note = (request.POST.get("issue_note") or "").strip()
    task.issue_note = note
    task.status = PickTask.Status.EXCEPTION
    task.save(update_fields=["issue_note", "status"])
    messages.warning(request, "Issue reported.")
    return redirect("/picking/")


@login_required
@role_required("admin", "manager")
def consolidation_queue(request):
    qs = Consolidation.objects.filter(status=Consolidation.Status.PENDING).select_related("order")
    return render(request, "consolidation/consolidation_queue.html", {"rows": qs, "today_str": now_date_str()})


@login_required
@role_required("admin", "manager")
def consolidation_detail(request, order_id: int):
    order = get_object_or_404(Order, pk=order_id)
    cons = get_object_or_404(Consolidation, order=order)
    picks = PickTask.objects.filter(order=order, status=PickTask.Status.COMPLETED).select_related("sku")
    return render(
        request,
        "consolidation/consolidation_detail.html",
        {"order": order, "consolidation": cons, "picks": picks, "today_str": now_date_str()},
    )


@login_required
@role_required("admin", "manager")
@require_http_methods(["POST"])
def consolidation_send_packing(request, order_id: int):
    order = get_object_or_404(Order, pk=order_id)
    cons = get_object_or_404(Consolidation, order=order)
    cons.status = Consolidation.Status.READY_FOR_PACKING
    cons.verified_at = timezone.now()
    cons.save(update_fields=["status", "verified_at"])
    order.status = Order.Status.PACKING
    order.save(update_fields=["status"])
    Packing.objects.get_or_create(order=order, defaults={"status": Packing.Status.PENDING})
    messages.success(request, "Sent to packing.")
    return redirect(f"/consolidation/{order.id}/")


@login_required
@role_required("packer")
def packing_queue(request):
    rows = Packing.objects.filter(status__in=[Packing.Status.PENDING, Packing.Status.IN_PROGRESS]).select_related(
        "order"
    )
    return render(request, "packing/packing_queue.html", {"rows": rows, "today_str": now_date_str()})


@login_required
@role_required("packer")
@require_http_methods(["GET", "POST"])
def packing_order_detail(request, order_id: int):
    order = get_object_or_404(Order, pk=order_id)
    packing = get_object_or_404(Packing, order=order)
    if request.method == "POST":
        if request.POST.get("verify") == "1":
            packing.verification_status = True
            packing.status = Packing.Status.IN_PROGRESS
            packing.save(update_fields=["verification_status", "status"])
            messages.success(request, "Items verified.")
    return render(request, "packing/packing_order_detail.html", {"order": order, "packing": packing, "today_str": now_date_str()})


@login_required
@role_required("packer")
@require_http_methods(["GET", "POST"])
def box_selection(request, order_id: int):
    order = get_object_or_404(Order, pk=order_id)
    packing = get_object_or_404(Packing, order=order)
    if not packing.verification_status:
        messages.error(request, "Verify items first.")
        return redirect(f"/packing/{order.id}/")
    if request.method == "POST":
        packing.box_type = (request.POST.get("box_type") or "").strip()
        if not packing.box_type:
            messages.error(request, "Please select a box type.")
            return redirect(f"/packing/{order.id}/box/")
        packing.is_box_selected = True
        packing.save(update_fields=["box_type", "is_box_selected"])
        return redirect(f"/packing/{order.id}/process/")
    return render(request, "packing/box_selection.html", {"order": order, "packing": packing, "today_str": now_date_str()})


@login_required
@role_required("packer")
@require_http_methods(["GET", "POST"])
def packing_process(request, order_id: int):
    order = get_object_or_404(Order, pk=order_id)
    packing = get_object_or_404(Packing, order=order)
    if not packing.verification_status:
        return redirect(f"/packing/{order.id}/")
    if not packing.is_box_selected:
        return redirect(f"/packing/{order.id}/box/")

    mismatch = False
    if request.method == "POST":
        for item in order.items.all():
            raw = request.POST.get(f"packed_{item.id}") or "0"
            try:
                packed_qty = int(raw)
            except Exception:
                packed_qty = 0
            if packed_qty != item.quantity:
                mismatch = True
                break
        if mismatch:
            messages.error(request, "Quantity mismatch found. Fix before label generation.")
        else:
            messages.success(request, "Packing process complete.")
            return redirect(f"/packing/{order.id}/label/")
    return render(request, "packing/packing_process.html", {"order": order, "packing": packing, "mismatch": mismatch, "today_str": now_date_str()})


@login_required
@role_required("packer")
@require_http_methods(["GET", "POST"])
def packing_label(request, order_id: int):
    order = get_object_or_404(Order, pk=order_id)
    packing = get_object_or_404(Packing, order=order)
    if request.method == "POST":
        packing.label_text = f"LBL-{order.order_id}"
        packing.packed_by = request.user
        packing.status = Packing.Status.COMPLETED
        packing.save(update_fields=["label_text", "packed_by", "status"])
        Shipment.objects.get_or_create(order=order, defaults={"status": Shipment.Status.PENDING})
        return redirect(f"/packing/{order.id}/success/")
    return render(request, "packing/label_generation.html", {"order": order, "packing": packing, "today_str": now_date_str()})


@login_required
@role_required("packer")
def packing_success(request, order_id: int):
    order = get_object_or_404(Order, pk=order_id)
    packing = get_object_or_404(Packing, order=order)
    return render(request, "packing/packing_success.html", {"order": order, "packing": packing, "today_str": now_date_str()})


@login_required
@role_required("admin", "manager")
def dispatch_queue(request):
    rows = (
        Order.objects.filter(packings__status=Packing.Status.COMPLETED)
        .exclude(status=Order.Status.COMPLETED)
        .distinct()
        .select_related("shipment")
    )
    return render(request, "shipping/dispatch_queue.html", {"orders": rows, "today_str": now_date_str()})


@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def shipment_detail(request, order_id: int):
    order = get_object_or_404(Order, pk=order_id)
    ship, _ = Shipment.objects.get_or_create(order=order)
    if request.method == "GET":
        return render(request, "shipping/shipment_detail.html", {"order": order, "shipment": ship, "today_str": now_date_str()})
    ship.carrier = (request.POST.get("carrier") or "").strip()
    ship.tracking_number = (request.POST.get("tracking_number") or "").strip()
    ship.truck_ref = (request.POST.get("truck_ref") or "").strip()
    ship.status = Shipment.Status.DISPATCHED
    ship.save(update_fields=["carrier", "tracking_number", "truck_ref", "status"])
    order.status = Order.Status.COMPLETED
    order.save(update_fields=["status"])
    messages.success(request, "Dispatched.")
    return redirect("/shipping/")


@login_required
@role_required("admin", "manager")
def returns_list(request):
    rows = Return.objects.select_related("order").order_by("-id")[:200]
    return render(request, "returns/returns_list.html", {"rows": rows, "today_str": now_date_str()})


@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def return_request(request):
    if request.method == "GET":
        orders = Order.objects.filter(status__in=[Order.Status.SHIPPED, Order.Status.COMPLETED]).order_by("-id")[:100]
        return render(request, "returns/return_request.html", {"orders": orders, "today_str": now_date_str()})
    oid = request.POST.get("order_id")
    reason = (request.POST.get("reason") or "").strip()
    order = get_object_or_404(Order, pk=oid)
    Return.objects.create(order=order, reason=reason)
    messages.success(request, "Return requested.")
    return redirect("/returns/")


@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def return_inspection(request, return_id: int):
    ret = get_object_or_404(Return, pk=return_id)
    if request.method == "GET":
        return render(request, "returns/return_inspection.html", {"ret": ret, "today_str": now_date_str()})
    ret.status = (request.POST.get("status") or Return.Status.INSPECTED)
    ret.inspection_notes = (request.POST.get("inspection_notes") or "").strip()
    ret.save(update_fields=["status", "inspection_notes"])
    messages.success(request, "Inspection saved.")
    return redirect("/returns/")


@login_required
@role_required("admin", "manager")
def user_list(request):
    users = User.objects.order_by("username")
    return render(request, "users/user_list.html", {"users": users, "today_str": now_date_str()})


@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def create_user(request):
    if request.method == "GET":
        return render(request, "users/create_user.html", {"today_str": now_date_str(), "roles": User.Role.choices})
    username = (request.POST.get("username") or "").strip()
    email = (request.POST.get("email") or "").strip()
    employee_id = (request.POST.get("employee_id") or "").strip()
    password = request.POST.get("password") or ""
    role = (request.POST.get("role") or User.Role.PICKER).strip()
    warehouse = (request.POST.get("warehouse") or "").strip()
    if not username or not employee_id or not password:
        messages.error(request, "Username, employee ID, and password are required.")
        return redirect("/users/create/")
    if User.objects.filter(username=username).exists():
        messages.error(request, "Username taken.")
        return redirect("/users/create/")
    u = User.objects.create_user(
        username=username,
        email=email,
        password=password,
    )
    u.employee_id = employee_id
    u.role = role
    u.warehouse = warehouse or None
    u.save()
    messages.success(request, "User created.")
    return redirect("/users/")


@login_required
@role_required("admin", "manager")
def role_permissions_page(request):
    return render(request, "settings/role_permissions.html", {"roles": User.Role.choices, "today_str": now_date_str()})


@login_required
@role_required("admin", "manager")
def system_settings(request):
    return render(request, "settings/system_settings.html", {"today_str": now_date_str()})


@login_required
@role_required("packer")
def packing_legacy(request):
    return redirect("/packing/queue/")


@login_required
@role_required("admin", "manager", "putaway")
def putaway(request):
    return redirect("/inbound/putaway/tasks/")


@login_required
@role_required("admin", "manager")
def warehouse_list(request):
    q = (request.GET.get("q") or "").strip()
    rows = Warehouse.objects.select_related("manager").order_by("code")
    if q:
        rows = rows.filter(Q(name__icontains=q) | Q(code__icontains=q))
    return render(request, "warehouse/warehouse_list.html", {"rows": rows, "q": q, "today_str": now_date_str()})


@login_required
@role_required("admin", "manager")
def warehouse_detail(request, warehouse_id: int):
    warehouse = get_object_or_404(Warehouse, pk=warehouse_id)
    return render(request, "warehouse/warehouse_detail.html", {"warehouse": warehouse, "today_str": now_date_str()})


@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def create_warehouse(request):
    if request.method == "POST":
        Warehouse.objects.create(
            name=(request.POST.get("name") or "").strip(),
            code=(request.POST.get("code") or "").strip().upper(),
            location=(request.POST.get("location") or "").strip(),
            contact=(request.POST.get("contact") or "").strip(),
            status=(request.POST.get("status") or Warehouse.Status.ACTIVE),
        )
        messages.success(request, "Warehouse created.")
        return redirect("/warehouse/")
    return render(request, "warehouse/create_warehouse.html", {"today_str": now_date_str()})


@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def zone_setup(request):
    if request.method == "POST":
        wh = get_object_or_404(Warehouse, pk=request.POST.get("warehouse_id"))
        Zone.objects.create(
            warehouse=wh,
            name=(request.POST.get("name") or "").strip(),
            code=(request.POST.get("code") or "").strip().upper(),
            type=(request.POST.get("type") or Zone.Type.BULK),
            temperature=(request.POST.get("temperature") or Zone.Temperature.NORMAL),
        )
        messages.success(request, "Zone created.")
        return redirect("/warehouse/zones/")
    return render(request, "warehouse/zone_setup.html", {
        "warehouses": Warehouse.objects.all(), 
        "zones": Zone.objects.select_related("warehouse").order_by("code"),
        "today_str": now_date_str()
    })


@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def rack_setup(request):
    if request.method == "POST":
        zone = get_object_or_404(Zone, pk=request.POST.get("zone_id"))
        total_levels = max(1, int(request.POST.get("levels") or 1))
        rack = Rack.objects.create(
            zone=zone,
            rack_number=(request.POST.get("rack_number") or "").strip().upper(),
            levels=total_levels,
            total_levels=total_levels,
            max_capacity=int(request.POST.get("max_capacity") or 0),
        )
        for i in range(1, total_levels + 1):
            Level.objects.get_or_create(rack=rack, level_number=i)
        messages.success(request, "Rack created.")
        return redirect("/warehouse/racks/")
    return render(request, "warehouse/rack_setup.html", {
        "zones": Zone.objects.select_related("warehouse"), 
        "racks": Rack.objects.select_related("zone__warehouse").order_by("rack_number"),
        "today_str": now_date_str()
    })


@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def bin_configuration(request):
    if request.method == "POST":
        rack = get_object_or_404(Rack.objects.select_related("zone__warehouse"), pk=request.POST.get("rack_id"))
        level = get_object_or_404(Level, pk=request.POST.get("level_id"), rack=rack)
        bins_per_level = max(1, int(request.POST.get("bins_per_level") or 1))
        size = request.POST.get("size") or Bin.Size.MEDIUM
        max_capacity = max(0, int(request.POST.get("max_capacity") or 0))
        generated = 0
        for b in range(1, bins_per_level + 1):
            code = f"{rack.zone.warehouse.code}-{rack.zone.code}-{rack.rack_number}-L{level.level_number}-B{b}"
            _, created = Bin.objects.get_or_create(
                bin_code=code,
                defaults={
                    "rack": rack,
                    "level": level,
                    "level_number": level.level_number,
                    "size": size,
                    "max_capacity": max_capacity,
                },
            )
            if created:
                generated += 1
        messages.success(request, f"{generated} bins generated.")
        return redirect("/warehouse/bins/")
    return render(request, "warehouse/bin_configuration.html", {
        "racks": Rack.objects.select_related("zone__warehouse"), 
        "levels": Level.objects.select_related("rack").order_by("rack_id", "level_number"),
        "bins": Bin.objects.select_related("level__rack__zone__warehouse").order_by("bin_code")[:200],
        "today_str": now_date_str()
    })


@login_required
@role_required("admin", "manager")
def warehouse_layout_map(request):
    return render(request, "warehouse/warehouse_layout_map.html", {"warehouses": Warehouse.objects.all(), "today_str": now_date_str()})


@login_required
@role_required("admin", "manager")
def sku_list(request):
    q = (request.GET.get("q") or "").strip()
    category = (request.GET.get("category") or "").strip()
    status = (request.GET.get("status") or "").strip()
    rows = SKU.objects.order_by("sku_code")
    if q:
        rows = rows.filter(Q(sku_code__icontains=q) | Q(name__icontains=q))
    if category:
        rows = rows.filter(category=category)
    if status == "active":
        rows = rows.filter(quantity__gt=0)
    elif status == "inactive":
        rows = rows.filter(quantity=0)
    categories = SKU.objects.exclude(category="").values_list("category", flat=True).distinct()
    return render(
        request,
        "inventory/sku_list.html",
        {"rows": rows, "q": q, "category": category, "status": status, "categories": categories, "today_str": now_date_str()},
    )


@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def create_sku(request):
    if request.method == "POST":
        code = (request.POST.get("code") or "").strip().upper()
        SKU.objects.create(
            sku_code=code,
            name=(request.POST.get("name") or "").strip(),
            category=(request.POST.get("category") or "").strip(),
            brand=(request.POST.get("brand") or "").strip(),
            unit_type=(request.POST.get("unit_type") or SKU.UnitType.UNIT),
            weight=request.POST.get("weight") or 0,
            dimensions=(request.POST.get("dimensions") or "").strip(),
            barcode=f"BAR-{code}",
            quantity=0,
        )
        messages.success(request, "SKU created.")
        return redirect("/inventory/skus/")
    return render(request, "inventory/create_sku.html", {"today_str": now_date_str(), "unit_types": SKU.UnitType.choices})


@login_required
@role_required("admin", "manager")
def sku_detail(request, sku_id: int):
    sku = get_object_or_404(SKU, pk=sku_id)
    inventory_rows = Inventory.objects.filter(sku=sku).select_related("warehouse", "zone", "bin__level__rack")
    totals = inventory_rows.aggregate(total=Sum("quantity"))
    allocated = (
        PickTask.objects.filter(sku=sku, status__in=[PickTask.Status.PENDING, PickTask.Status.IN_PROGRESS]).aggregate(
            total=Sum("quantity")
        ).get("total")
        or 0
    )
    total_qty = totals.get("total") or 0
    return render(
        request,
        "inventory/sku_detail.html",
        {
            "sku": sku,
            "inventory_rows": inventory_rows,
            "total_qty": total_qty,
            "allocated_qty": allocated,
            "available_qty": max(0, total_qty - allocated),
            "today_str": now_date_str(),
        },
    )


@login_required
@role_required("admin", "manager")
def inventory_list(request):
    rows = Inventory.objects.select_related("sku", "warehouse", "zone", "bin__level__rack__zone__warehouse").order_by("-id")
    warehouse = request.GET.get("warehouse") or ""
    zone = request.GET.get("zone") or ""
    category = request.GET.get("category") or ""
    if warehouse:
        rows = rows.filter(warehouse_id=warehouse)
    if zone:
        rows = rows.filter(zone_id=zone)
    if category:
        rows = rows.filter(sku__category=category)
    return render(
        request,
        "inventory/inventory_list.html",
        {
            "rows": rows,
            "warehouses": Warehouse.objects.order_by("code"),
            "zones": Zone.objects.order_by("code"),
            "categories": SKU.objects.exclude(category="").values_list("category", flat=True).distinct(),
            "warehouse_filter": warehouse,
            "zone_filter": zone,
            "category_filter": category,
            "today_str": now_date_str(),
        },
    )


@login_required
@role_required("admin", "manager")
@require_http_methods(["GET", "POST"])
def stock_adjustment(request):
    if request.method == "POST":
        sku_id = request.POST.get("sku_id")
        bin_id = request.POST.get("bin_id")
        inv = Inventory.objects.filter(sku_id=sku_id, bin_id=bin_id).first()
        if not inv:
            messages.error(request, "Inventory record not found for selected SKU and bin.")
            return redirect("/inventory/adjustment/")
        qty = max(1, int(request.POST.get("quantity") or 1))
        adjustment_type = request.POST.get("adjustment_type") or StockAdjustment.AdjustmentType.REMOVE
        if adjustment_type == StockAdjustment.AdjustmentType.ADD:
            inv.quantity += qty
            inv.bin.current_capacity += qty
        else:
            inv.quantity = max(0, inv.quantity - qty)
            inv.bin.current_capacity = max(0, inv.bin.current_capacity - qty)
        inv.bin.save(update_fields=["current_capacity"])
        inv.save(update_fields=["quantity"])
        inv.sku.quantity = int(sum([i.quantity for i in Inventory.objects.filter(sku=inv.sku)]))
        inv.sku.save(update_fields=["quantity"])
        StockAdjustment.objects.create(
            sku=inv.sku,
            bin=inv.bin,
            adjustment_type=adjustment_type,
            quantity=qty,
            reason=(request.POST.get("reason") or "").strip(),
            created_by=request.user,
        )
        messages.success(request, "Stock adjusted.")
        return redirect("/inventory/adjustment/")
    return render(
        request,
        "inventory/stock_adjustment.html",
        {
            "rows": Inventory.objects.select_related("sku", "warehouse", "bin"),
            "skus": SKU.objects.order_by("sku_code"),
            "bins": Bin.objects.select_related("level__rack__zone__warehouse").order_by("bin_code"),
            "today_str": now_date_str(),
        },
    )


@login_required
@role_required("admin", "manager")
def barcode_generator(request):
    rows = SKU.objects.order_by("sku_code")
    return render(request, "inventory/barcode_generator.html", {"rows": rows, "today_str": now_date_str()})


@login_required
@role_required("admin", "manager")
def packaging_hierarchy(request):
    return render(request, "inventory/packaging_hierarchy.html", {"rows": SKU.objects.order_by("sku_code"), "today_str": now_date_str()})
