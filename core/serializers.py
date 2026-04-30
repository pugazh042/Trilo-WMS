from .models import Bin, Inventory, Level, Rack, SKU, Warehouse, Zone


def warehouse_serializer(obj: Warehouse) -> dict:
    return {
        "id": obj.id,
        "name": obj.name,
        "code": obj.code,
        "location": obj.location,
        "manager_id": obj.manager_id,
        "created_at": getattr(obj, "created_at", None),
    }


def zone_serializer(obj: Zone) -> dict:
    return {
        "id": obj.id,
        "warehouse_id": obj.warehouse_id,
        "name": obj.name,
        "code": obj.code,
        "type": obj.type,
        "temperature": obj.temperature,
    }


def rack_serializer(obj: Rack) -> dict:
    return {
        "id": obj.id,
        "zone_id": obj.zone_id,
        "rack_number": obj.rack_number,
        "total_levels": obj.total_levels,
    }


def level_serializer(obj: Level) -> dict:
    return {"id": obj.id, "rack_id": obj.rack_id, "level_number": obj.level_number}


def bin_serializer(obj: Bin) -> dict:
    return {
        "id": obj.id,
        "level_id": obj.level_id,
        "bin_code": obj.bin_code,
        "size": obj.size,
        "max_capacity": obj.max_capacity,
        "current_capacity": obj.current_capacity,
    }


def sku_serializer(obj: SKU) -> dict:
    return {
        "id": obj.id,
        "name": obj.name,
        "code": obj.sku_code,
        "category": obj.category,
        "weight": float(obj.weight),
        "dimensions": obj.dimensions,
        "barcode": obj.barcode,
    }


def inventory_serializer(obj: Inventory) -> dict:
    return {
        "id": obj.id,
        "sku": obj.sku.sku_code,
        "quantity": obj.quantity,
        "status": obj.status,
        "warehouse": obj.warehouse.code,
        "zone": obj.zone.code if obj.zone_id else "",
        "bin": obj.bin.bin_code,
    }
