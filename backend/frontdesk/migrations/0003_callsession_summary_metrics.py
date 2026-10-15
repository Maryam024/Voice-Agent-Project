from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("frontdesk", "0002_doctor_fee"),
    ]

    operations = [
        migrations.AddField(
            model_name="callsession",
            name="ended_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="callsession",
            name="summary",
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AddField(
            model_name="callsession",
            name="outcome",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="callsession",
            name="metrics",
            field=models.JSONField(default=list),
        ),
    ]