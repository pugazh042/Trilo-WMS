# Generated manually to move post-initial Bin capacity fields into an applied schema migration.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="bin",
            name="weight_capacity",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name="bin",
            name="volume_capacity",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
    ]
