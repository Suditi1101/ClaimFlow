from django.db import migrations, models
import policies.models


class Migration(migrations.Migration):
    dependencies = [("policies", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="policy",
            name="reference",
            field=models.CharField(
                default=policies.models.generate_policy_reference,
                editable=False,
                help_text="Generated automatically when the policy is created.",
                max_length=60,
                unique=True,
            ),
        ),
    ]
