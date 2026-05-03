from django.apps import apps
from django.contrib.auth import get_user_model

User = get_user_model()

print("Truncating database...")

# We need to disable foreign key checks temporarily in SQLite to avoid cascading issues or constraint failures
# Or we can just delete in reverse dependency order, but deleting everything in a loop is usually fine if cascading is on.
# However, if we delete a model, it might cascade and delete related objects anyway.
# To be safe, we just loop and catch errors, or loop multiple times.

models = list(apps.get_app_config('core').get_models())
models_to_delete = [m for m in models if m != User]

# Try deleting in multiple passes to handle FK constraints
for _ in range(3):
    for model in models_to_delete:
        try:
            count = model.objects.count()
            if count > 0:
                model.objects.all().delete()
                print(f"Deleted {count} from {model.__name__}")
        except Exception as e:
            pass

print("Done truncating data.")
