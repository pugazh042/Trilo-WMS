"""Outbound dispatch: carrier → truck → confirmation."""

from __future__ import annotations

import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date

from .decorators import role_required
from .models import Carrier, Order, Packing, Shipment, ShipmentItem, TruckAssignment
from .utils import now_date_str


def _ensure_shipment_with_items(order: Order) -> Shipment:
    ship, _ = Shipment.objects.get_or_create(order=order, defaults={"status": Shipment.Status.PENDING})
    if not ship.items.exists():
        for oi in order.items.all():
            ShipmentItem.objects.create(shipment=ship, sku=oi.sku, quantity=oi.quantity)
    return ship


@login_required
@role_required("admin", "manager")
def carrier_selection(request, order_id: int):
    order = get_object_or_404(Order, pk=order_id)
    if not Packing.objects.filter(order=order, status=Packing.Status.COMPLETED).exists():
        messages.error(request, "Order must be packed before dispatch.")
        return redirect("/shipping/")

    ship = _ensure_shipment_with_items(order)
    carriers = Carrier.objects.order_by("name")

    if request.method == "POST":
        if request.POST.get("action") == "add_carrier":
            name = (request.POST.get("carrier_name") or "").strip()
            if not name:
                messages.error(request, "Carrier name is required.")
            else:
                Carrier.objects.create(
                    name=name,
                    contact=(request.POST.get("carrier_contact") or "").strip(),
                    service_type=(request.POST.get("service_type") or "").strip(),
                )
                messages.success(request, "Carrier added.")
            return redirect(f"/shipping/{order.id}/carrier/")

        cid = request.POST.get("carrier_id")
        est = (request.POST.get("estimated_delivery") or "").strip()
        car = get_object_or_404(Carrier, pk=cid) if cid else None
        ship.carrier_partner = car
        ship.carrier = car.name if car else ""
        if est:
            ship.estimated_delivery = parse_date(est)
        ship.status = Shipment.Status.READY
        ship.save(update_fields=["carrier_partner", "carrier", "estimated_delivery", "status"])
        messages.success(request, "Carrier selected.")
        return redirect(f"/shipping/{order.id}/truck/")

    return render(
        request,
        "shipping/carrier_selection.html",
        {
            "order": order,
            "shipment": ship,
            "carriers": carriers,
            "today_str": now_date_str(),
        },
    )


@login_required
@role_required("admin", "manager")
def truck_assignment_view(request, order_id: int):
    order = get_object_or_404(Order, pk=order_id)
    ship = get_object_or_404(Shipment, order=order)
    if ship.status not in (Shipment.Status.READY, Shipment.Status.PENDING):
        messages.error(request, "Invalid shipment state.")
        return redirect("/shipping/")

    ta, _ = TruckAssignment.objects.get_or_create(shipment=ship)

    if request.method == "POST":
        ta.truck_number = (request.POST.get("truck_number") or "").strip()
        ta.driver_name = (request.POST.get("driver_name") or "").strip()
        ta.driver_contact = (request.POST.get("driver_contact") or "").strip()
        ta.dock_number = (request.POST.get("dock_number") or "").strip()
        ta.save()
        ship.truck_ref = ta.truck_number
        ship.save(update_fields=["truck_ref"])
        messages.success(request, "Truck assignment saved.")
        return redirect(f"/shipping/{order.id}/confirm/")

    return render(
        request,
        "shipping/truck_assignment.html",
        {"order": order, "shipment": ship, "ta": ta, "today_str": now_date_str()},
    )


@login_required
@role_required("admin", "manager")
def dispatch_confirmation(request, order_id: int):
    order = get_object_or_404(Order, pk=order_id)
    ship = get_object_or_404(Shipment, order=order)
    ta = getattr(ship, "truck", None)

    if request.method == "POST":
        with transaction.atomic():
            ship.tracking_number = f"TRK-{secrets.token_hex(4).upper()}"
            ship.status = Shipment.Status.DISPATCHED
            ship.save(update_fields=["tracking_number", "status"])
            order.status = Order.Status.COMPLETED
            order.save(update_fields=["status"])
        messages.success(request, "Dispatch confirmed. Tracking generated.")
        return redirect("/shipping/")

    items = ship.items.select_related("sku")
    return render(
        request,
        "shipping/dispatch_confirmation.html",
        {
            "order": order,
            "shipment": ship,
            "ta": ta,
            "items": items,
            "today_str": now_date_str(),
        },
    )


@login_required
@role_required("admin", "manager")
def shipment_detail_legacy(request, order_id: int):
    return redirect(f"/shipping/{order_id}/carrier/")
