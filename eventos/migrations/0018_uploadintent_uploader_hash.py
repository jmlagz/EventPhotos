from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("eventos", "0017_evento_creation_source_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="uploadintent",
            name="uploader_hash",
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=64,
                null=True,
            ),
        ),
    ]
