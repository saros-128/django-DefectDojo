from django.db import migrations, models
import django.db.models.deletion


def refuse_grouped_downgrade(apps, schema_editor):
    links = apps.get_model("auditor_bridge", "RepositoryLink")
    duplicates = links.objects.values("product_id").annotate(count=models.Count("pk")).filter(count__gt=1)
    if duplicates.exists():
        msg = "Cannot restore one-repository-per-product schema while grouped bindings exist; restore a compatible database backup."
        raise RuntimeError(msg)


class Migration(migrations.Migration):
    dependencies = [("auditor_bridge", "0002_scanimport_commit_sha")]

    operations = [
        migrations.AddField(
            model_name="scanimport", name="coverage_status",
            field=models.CharField(default="unknown", max_length=16),
        ),
        migrations.AlterField(
            model_name="repositorylink", name="product",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="dojo.product"),
        ),
        migrations.AddField(
            model_name="repositorylink", name="product_managed",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(migrations.RunPython.noop, refuse_grouped_downgrade),
    ]
