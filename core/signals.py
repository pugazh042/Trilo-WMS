from django.db.models.signals import post_migrate
from django.dispatch import receiver


@receiver(post_migrate)
def seed_default_workers(sender, **kwargs):
    if sender.name != "core":
        return
    from .models import User, Warehouse

    wh, _ = Warehouse.objects.get_or_create(
        code="W1",
        defaults={"name": "Main Warehouse", "location": "HQ", "contact": "N/A"},
    )
    users = [
        ("ADM001", "admin", "admin", User.Role.ADMIN),
        ("MGR001", "manager", "manager", User.Role.MANAGER),
        ("PKR001", "picker1", "picker1", User.Role.PICKER),
        ("PKR002", "picker2", "picker2", User.Role.PICKER),
        ("PCK001", "packer1", "packer1", User.Role.PACKER),
        ("PCK002", "packer2", "packer2", User.Role.PACKER),
        ("PUT001", "putaway1", "putaway1", User.Role.PUTAWAY),
        ("PUT002", "putaway2", "putaway2", User.Role.PUTAWAY),
    ]
    for employee_id, username, password, role in users:
        user = User.objects.filter(employee_id=employee_id).first()
        if not user:
            user = User.objects.filter(username=username).first()
        if not user:
            user = User(employee_id=employee_id, username=username)
        user.employee_id = employee_id
        user.username = username
        user.role = role
        user.assigned_warehouse = wh
        if not user.pk:
            user.set_password(password)
        user.save()
