from django.db import migrations, models


def seed_metric_counters(apps, schema_editor):
    MetricCounter = apps.get_model("core", "MetricCounter")
    for metric_name in ["spans_ingested_total", "eval_tasks_completed_total"]:
        MetricCounter.objects.get_or_create(name=metric_name, defaults={"value": 0})


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_remove_span_span_end_after_start_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="MetricCounter",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=100, unique=True)),
                ("value", models.BigIntegerField(default=0)),
            ],
        ),
        migrations.AddConstraint(
            model_name="metriccounter",
            constraint=models.CheckConstraint(
                condition=models.Q(("value__gte", 0)),
                name="metric_counter_value_non_negative",
            ),
        ),
        migrations.RunPython(seed_metric_counters, migrations.RunPython.noop),
    ]
