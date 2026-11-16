"""0054: Oracle Health rows get patient/Binary.read in their extra scopes."""

from importlib import import_module

from django.apps import apps

from core.models import EhrVendor

migration = import_module("core.migrations.0054_ehrvendor_oracle_health_binary_read")


def test_oracle_health_rows_get_binary_read_and_keep_what_an_operator_set(db):
    EhrVendor.objects.create(name="Oracle Health Sandbox")
    EhrVendor.objects.create(name="Oracle Health Production", extra_scopes="profile")
    EhrVendor.objects.create(name="Some Other EHR")

    migration.add_binary_read(apps, None)
    migration.add_binary_read(apps, None)  # running twice adds it once

    scopes = dict(EhrVendor.objects.values_list("name", "extra_scopes"))
    assert scopes == {
        "Oracle Health Sandbox": "patient/Binary.read",
        "Oracle Health Production": "profile patient/Binary.read",
        "Some Other EHR": "",
    }
