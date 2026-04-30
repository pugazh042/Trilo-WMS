# Generated manually for Trilo WMS full module rollout

import secrets

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def gen_order_id():
    return f"ORD-{secrets.token_hex(4).upper()}"


def fill_order_ids(apps, schema_editor):
    Order = apps.get_model("core", "Order")
    seen = set()
    for o in Order.objects.all():
        if not getattr(o, "order_id", None):
            oid = gen_order_id()
            while oid in seen or Order.objects.filter(order_id=oid).exists():
                oid = gen_order_id()
            seen.add(oid)
            o.order_id = oid
            o.save(update_fields=["order_id"])


def map_order_status(apps, schema_editor):
    Order = apps.get_model("core", "Order")
    mapping = {
        "new": "pending",
        "processing": "pending",
        "picking": "picking",
        "packing": "packing",
        "shipped": "shipped",
        "cancelled": "cancelled",
    }
    for o in Order.objects.all():
        s = getattr(o, "status", "") or ""
        if s in mapping:
            o.status = mapping[s]
            o.save(update_fields=["status"])


def map_picktask_status(apps, schema_editor):
    PickTask = apps.get_model("core", "PickTask")
    mapping = {
        "open": "pending",
        "in_progress": "in_progress",
        "done": "completed",
        "exception": "exception",
    }
    for t in PickTask.objects.all():
        s = getattr(t, "status", "") or ""
        if s in mapping:
            t.status = mapping[s]
            t.save(update_fields=["status"])


def fill_picktask_sku(apps, schema_editor):
    PickTask = apps.get_model("core", "PickTask")
    for t in PickTask.objects.select_related("order_item").all():
        if t.order_item_id and not getattr(t, "sku_id", None):
            t.sku_id = t.order_item.sku_id
            loc = ""
            try:
                sb = t.stock_bin
                if sb and sb.location_id:
                    loc = sb.location.code
            except Exception:
                pass
            t.source_location = loc
            t.save(update_fields=["sku_id", "source_location"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_location_order_allocation_status_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="order_id",
            field=models.CharField(db_index=True, max_length=40, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="order",
            name="customer_phone",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="order",
            name="on_hold",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="allocated_quantity",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(fill_order_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="order",
            name="order_id",
            field=models.CharField(db_index=True, max_length=40, unique=True),
        ),
        migrations.AlterField(
            model_name="order",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("allocated", "Allocated"),
                    ("picking", "Picking"),
                    ("consolidating", "Consolidating"),
                    ("packing", "Packing"),
                    ("shipped", "Shipped"),
                    ("completed", "Completed"),
                    ("cancelled", "Cancelled"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.RunPython(map_order_status, migrations.RunPython.noop),
        migrations.RenameField(
            model_name="picktask",
            old_name="required_qty",
            new_name="quantity",
        ),
        migrations.RenameField(
            model_name="picktask",
            old_name="assigned_to",
            new_name="picker",
        ),
        migrations.AddField(
            model_name="picktask",
            name="sku",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="pick_tasks",
                to="core.sku",
            ),
        ),
        migrations.AddField(
            model_name="picktask",
            name="source_location",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="picktask",
            name="issue_note",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AlterField(
            model_name="picktask",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("in_progress", "In Progress"),
                    ("completed", "Completed"),
                    ("exception", "Exception"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.RunPython(map_picktask_status, migrations.RunPython.noop),
        migrations.RunPython(fill_picktask_sku, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="picktask",
            name="sku",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="pick_tasks",
                to="core.sku",
            ),
        ),
        migrations.CreateModel(
            name="Consolidation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("ready_for_packing", "Ready for Packing"),
                            ("done", "Done"),
                        ],
                        default="pending",
                        max_length=30,
                    ),
                ),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "order",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="consolidation",
                        to="core.order",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Packing",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("box_type", models.CharField(blank=True, default="", max_length=80)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("in_progress", "In Progress"),
                            ("completed", "Completed"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("label_text", models.CharField(blank=True, default="", max_length=200)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "order",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="packings", to="core.order"),
                ),
                (
                    "packed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="packings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Shipment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("carrier", models.CharField(blank=True, default="", max_length=120)),
                ("tracking_number", models.CharField(blank=True, default="", max_length=120)),
                ("truck_ref", models.CharField(blank=True, default="", max_length=120)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("dispatched", "Dispatched"),
                            ("delivered", "Delivered"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "order",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shipment",
                        to="core.order",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Return",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reason", models.TextField(blank=True, default="")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("requested", "Requested"),
                            ("received", "Received"),
                            ("inspected", "Inspected"),
                            ("restocked", "Restocked"),
                            ("rejected", "Rejected"),
                        ],
                        default="requested",
                        max_length=20,
                    ),
                ),
                ("inspection_notes", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "order",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="returns", to="core.order"),
                ),
            ],
        ),
    ]
