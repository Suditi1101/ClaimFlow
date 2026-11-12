from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("claims", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="claimdocument",
            name="category",
            field=models.CharField(
                max_length=20,
                default="claim",
                choices=[
                    ("claim", "Claim document"),
                    ("supporting", "Supporting document"),
                ],
            ),
        )
    ]
