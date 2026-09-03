from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("eventos", "0012_uploadintent_finalization_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="uploadintent",
            name="cleaned_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
