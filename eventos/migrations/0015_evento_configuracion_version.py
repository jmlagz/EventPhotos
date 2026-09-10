from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("eventos", "0014_slideshow_promo"),
    ]

    operations = [
        migrations.AddField(
            model_name="evento",
            name="configuracion_version",
            field=models.PositiveSmallIntegerField(
                blank=True,
                default=None,
                null=True,
            ),
        ),
    ]
