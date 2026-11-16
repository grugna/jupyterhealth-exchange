# Moves what USCDI Patient Access knew about Epic from code onto the EhrVendor row, so an operator
# can see and set it per row. The data step sets the Epic rows the code matched by name; the reverse
# leaves the rows alone, since the columns it removes are all the step wrote.

from django.db import migrations, models

EPIC_ROW_NAMES = ("Epic Sandbox", "Epic Production")


def set_epic_rows(apps, schema_editor):
    EhrVendor = apps.get_model("core", "EhrVendor")
    EhrVendor.objects.filter(name__in=EPIC_ROW_NAMES).update(skip_type_checklist=True, extra_scopes="profile")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0052_drop_identifier_columns"),
    ]

    operations = [
        migrations.AddField(
            model_name="ehrvendor",
            name="skip_type_checklist",
            field=models.BooleanField(
                default=False,
                help_text="The EHR's own authorization screen asks the patient which record types to share, so "
                "JHE skips its checklist and requests every supported scope (Epic).",
            ),
        ),
        migrations.AddField(
            model_name="ehrvendor",
            name="extra_scopes",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Space-separated scopes requested in addition to openid fhirUser launch/patient, for "
                "an EHR whose sign-in or records need more (Epic: profile).",
            ),
        ),
        migrations.RunPython(set_epic_rows, migrations.RunPython.noop),
    ]
