# Oracle Health (Cerner Millennium) serves a document's content only to a token that also carries
# patient/Binary.read. Adds that scope to the Oracle Health rows' extra_scopes, keeping anything an
# operator already set there. The reverse leaves the rows alone.

from django.db import migrations

ORACLE_HEALTH_ROW_NAMES = ("Oracle Health Sandbox", "Oracle Health Production")
BINARY_READ = "patient/Binary.read"


def add_binary_read(apps, schema_editor):
    EhrVendor = apps.get_model("core", "EhrVendor")
    for vendor in EhrVendor.objects.filter(name__in=ORACLE_HEALTH_ROW_NAMES):
        scopes = vendor.extra_scopes.split()
        if BINARY_READ not in scopes:
            vendor.extra_scopes = " ".join([*scopes, BINARY_READ])
            vendor.save(update_fields=["extra_scopes"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0053_ehrvendor_patient_access_settings"),
    ]

    operations = [
        migrations.RunPython(add_binary_read, migrations.RunPython.noop),
    ]
