"""Tests for the EHR brands importer (SMART user-access Brands Bundle -> DB)."""

import io
import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.management.commands.import_ehr_brands import DEFAULT_SAMPLE, KNOWN_VENDORS, import_brands_bundle
from core.models import EhrBrand, EhrBrandLocation, EhrVendor

EPIC_SANDBOX_URL = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4/"


def _import_known(bundle, key):
    spec = KNOWN_VENDORS[key]
    return import_brands_bundle(
        bundle,
        spec.vendor_name,
        sandbox_vendor_name=spec.sandbox_vendor_name,
        sandbox_base_url=spec.sandbox_base_url,
    )


def _import_epic(bundle):
    return _import_known(bundle, "epic")


def _command(*args):
    out = io.StringIO()
    call_command("import_ehr_brands", *args, stdout=out)
    return out.getvalue()


def _bundle():
    """A minimal SMART user-access Brands Bundle: 1 brand, 1 endpoint, 2 facilities."""
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {
                "fullUrl": "https://example.org/Endpoint/ep1",
                "resource": {
                    "resourceType": "Endpoint",
                    "id": "ep1",
                    "address": "https://fhir.mountsinai.org/api/FHIR/R4",
                    "connectionType": {"code": "hl7-fhir-rest"},
                },
            },
            {
                "resource": {
                    "resourceType": "Organization",
                    "id": "brand-msinai",
                    "name": "Mount Sinai",
                    "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": "1234567890"}],
                    "endpoint": [{"reference": "Endpoint/ep1"}],
                },
            },
            {
                "resource": {
                    "resourceType": "Organization",
                    "id": "loc-1",
                    "name": "Mount Sinai Hospital",
                    "partOf": {"reference": "Organization/brand-msinai"},
                    "address": [
                        {
                            "text": "1 Gustave L. Levy Pl, New York, NY 10029",
                            "city": "New York",
                            "state": "NY",
                            "postalCode": "10029",
                        }
                    ],
                },
            },
            {
                "resource": {
                    "resourceType": "Organization",
                    "id": "loc-2",
                    "name": "Mount Sinai West",
                    "partOf": {"reference": "Organization/brand-msinai"},
                    "address": [{"city": "New York", "state": "NY", "postalCode": "10019"}],
                },
            },
        ],
    }


def test_imports_brand_and_locations(db):
    counts = _import_epic(_bundle())

    brand = EhrBrand.objects.get(fhir_base_url="https://fhir.mountsinai.org/api/FHIR/R4")
    assert brand.name == "Mount Sinai"
    assert brand.npi_type_2 == "1234567890"
    assert brand.locations.count() == 2
    assert counts["brands"] == 1
    assert counts["locations"] == 2
    # A brand with no "sandbox" in its name is a real customer -> Epic Production, never the
    # sandbox vendor whose ehr_client_id only works against Epic's shared test FHIR server.
    assert brand.vendor.name == "Epic Production"


def test_sandbox_brand_gets_the_sandbox_vendor(db):
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "id": "ep-sandbox",
                    "address": "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4/",
                }
            },
            {
                "resource": {
                    "resourceType": "Organization",
                    "id": "brand-sandbox",
                    "name": "Epic Health System (Sandbox)",
                    "endpoint": [{"reference": "Endpoint/ep-sandbox"}],
                }
            },
        ],
    }
    _import_epic(bundle)
    brand = EhrBrand.objects.get(fhir_base_url="https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4/")
    assert brand.vendor.name == "Epic Sandbox"


def test_production_and_sandbox_brands_share_only_their_own_vendor(db):
    # Importing the curated sample (sandbox + AACI + Mount Sinai) must produce exactly the two
    # vendors, not one per brand and not one shared "Epic" vendor for everything.
    bundle = json.loads(Path(DEFAULT_SAMPLE).read_text(encoding="utf-8"))
    _import_epic(bundle)

    assert set(EhrVendor.objects.values_list("name", flat=True)) == {"Epic Production", "Epic Sandbox"}
    sandbox = EhrVendor.objects.get(name="Epic Sandbox")
    production = EhrVendor.objects.get(name="Epic Production")
    assert list(sandbox.brands.values_list("name", flat=True)) == ["Epic Health System (Sandbox)"]
    assert set(production.brands.values_list("name", flat=True)) == {
        "Asian Americans for Community Involvement (AACI)",
        "Mount Sinai Health System",
    }

    # Re-importing must not create a second "Epic Production"/"Epic Sandbox" row.
    _import_epic(bundle)
    assert EhrVendor.objects.count() == 2


def test_new_epic_rows_get_epics_sign_in_settings_and_a_reimport_keeps_an_operators_edits(db):
    bundle = json.loads(Path(DEFAULT_SAMPLE).read_text(encoding="utf-8"))
    _import_epic(bundle)
    for vendor in EhrVendor.objects.all():
        assert vendor.skip_type_checklist is True and vendor.extra_scopes == "profile"

    EhrVendor.objects.filter(name="Epic Production").update(skip_type_checklist=False, extra_scopes="")
    _import_epic(bundle)
    production = EhrVendor.objects.get(name="Epic Production")
    assert production.skip_type_checklist is False and production.extra_scopes == ""


def test_import_is_idempotent(db):
    _import_epic(_bundle())
    _import_epic(_bundle())
    assert EhrBrand.objects.count() == 1
    assert EhrBrandLocation.objects.count() == 2


def test_brand_without_facility_gets_fallback_location(db):
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "id": "ep9",
                    "address": "https://solo.example.org/FHIR/R4",
                }
            },
            {
                "resource": {
                    "resourceType": "Organization",
                    "id": "brand-solo",
                    "name": "Solo Clinic",
                    "endpoint": [{"reference": "Endpoint/ep9"}],
                    "address": [{"text": "500 Main St", "state": "CA"}],
                }
            },
        ],
    }
    _import_epic(bundle)
    brand = EhrBrand.objects.get(fhir_base_url="https://solo.example.org/FHIR/R4")
    assert brand.locations.count() == 1
    assert brand.locations.first().name == "Solo Clinic"


def test_call_command_reads_file(db, tmp_path):
    path = tmp_path / "brands.json"
    path.write_text(json.dumps(_bundle()))
    _command("--file", str(path), "--epic")
    assert EhrBrand.objects.count() == 1
    assert EhrBrandLocation.objects.count() == 2


def _endpoint_per_organization_bundle():
    """The shape Oracle Health publishes: each Organization with its own Endpoint, no facilities."""
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {"resource": {"resourceType": "Endpoint", "id": "t1", "address": "https://fhir.example.com/r4/t1/"}},
            {
                "resource": {
                    "resourceType": "Organization",
                    "id": "Ot1",
                    "name": "Lakeside Clinic",
                    "address": [{"line": ["1 Main St"], "city": "Springfield", "state": "IL", "postalCode": "62701"}],
                    "endpoint": [{"reference": "Endpoint/t1"}],
                }
            },
        ],
    }


def test_oracle_health_takes_every_brand_in_the_bundle(db):
    counts = _import_known(_endpoint_per_organization_bundle(), "oracle_health")
    assert (counts["brands"], counts["locations"]) == (1, 1)
    brand = EhrBrand.objects.get(fhir_base_url="https://fhir.example.com/r4/t1/")
    assert brand.vendor.name == "Oracle Health Production"
    assert not EhrVendor.objects.filter(name__startswith="Epic").exists()
    location = brand.locations.get()  # no facilities listed: the brand is its own location
    assert (location.name, location.city, location.state) == ("Lakeside Clinic", "Springfield", "IL")


def test_an_existing_row_is_reused_and_keeps_its_client_id(db):
    EhrVendor.objects.create(name="Oracle Health Production", ehr_client_id="registered-client")
    _import_known(_endpoint_per_organization_bundle(), "oracle_health")
    vendor = EhrVendor.objects.get(name="Oracle Health Production")
    assert vendor.ehr_client_id == "registered-client" and vendor.brands.count() == 1


def test_a_named_vendor_does_not_get_epics_sign_in_settings(db):
    import_brands_bundle(_endpoint_per_organization_bundle(), "Some Other EHR")
    vendor = EhrVendor.objects.get()
    assert vendor.skip_type_checklist is False and vendor.extra_scopes == ""


def test_a_new_oracle_health_row_gets_binary_read_and_a_reimport_keeps_an_operators_edits(db):
    _import_known(_endpoint_per_organization_bundle(), "oracle_health")
    vendor = EhrVendor.objects.get()
    assert vendor.skip_type_checklist is False and vendor.extra_scopes == "patient/Binary.read"

    EhrVendor.objects.update(extra_scopes="")
    _import_known(_endpoint_per_organization_bundle(), "oracle_health")
    assert EhrVendor.objects.get().extra_scopes == ""


def test_call_command_takes_oracle_health(db, tmp_path):
    path = tmp_path / "brands.json"
    path.write_text(json.dumps(_endpoint_per_organization_bundle()))
    _command("--file", str(path), "--oracle-health")
    assert EhrBrand.objects.get().vendor.name == "Oracle Health Production"


def test_imports_facility_with_urn_uuid_partof_reference(db):
    """Epic's real production Brands Bundle uses urn:uuid: references (not
    Organization/<id>) for both endpoint and partOf links."""
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            {
                "fullUrl": "urn:uuid:ep-1",
                "resource": {
                    "resourceType": "Endpoint",
                    "id": "ep-1",
                    "address": "https://fhir.example.org/api/FHIR/R4",
                },
            },
            {
                "fullUrl": "urn:uuid:brand-1",
                "resource": {
                    "resourceType": "Organization",
                    "id": "brand-1",
                    "name": "Example Health",
                    "endpoint": [{"reference": "urn:uuid:ep-1"}],
                },
            },
            {
                "fullUrl": "urn:uuid:loc-1",
                "resource": {
                    "resourceType": "Organization",
                    "id": "loc-1",
                    "name": "Example Health Downtown Campus",
                    "partOf": {"reference": "urn:uuid:brand-1"},
                    "address": [{"text": "1 Example Way", "city": "Springfield", "state": "IL"}],
                },
            },
        ],
    }
    counts = _import_epic(bundle)
    brand = EhrBrand.objects.get(fhir_base_url="https://fhir.example.org/api/FHIR/R4")
    assert counts["locations"] == 1
    assert brand.locations.get().name == "Example Health Downtown Campus"


def test_shipped_sample_fixture_is_valid_and_imports(db):
    bundle = json.loads(Path(DEFAULT_SAMPLE).read_text(encoding="utf-8"))
    counts = _import_epic(bundle)
    assert counts["brands"] >= 1
    assert counts["locations"] >= 1
    # every seeded brand is selectable (has at least one location for the picker)
    assert all(b.locations.exists() for b in EhrBrand.objects.all())


def _brands(*brands):
    """A bundle of brands, each (name, base URL), with no facilities."""
    entry = []
    for i, (name, url) in enumerate(brands):
        entry.append({"resource": {"resourceType": "Endpoint", "id": f"ep{i}", "address": url}})
        entry.append(
            {
                "resource": {
                    "resourceType": "Organization",
                    "id": f"org{i}",
                    "name": name,
                    "endpoint": [{"reference": f"Endpoint/ep{i}"}],
                }
            }
        )
    return {"resourceType": "Bundle", "entry": entry}


def _write(tmp_path, bundle):
    path = tmp_path / "brands.json"
    path.write_text(json.dumps(bundle))
    return str(path)


def test_epic_files_its_public_sandbox_by_url_and_skips_every_other_sandbox(db, tmp_path):
    bundle = _brands(
        ("Epic Sandbox - Madison Campus", EPIC_SANDBOX_URL),
        ("Mercy Health Sandbox", "https://mercy.example.org/sandbox/FHIR/R4/"),
        ("Mercy Health", "https://mercy.example.org/FHIR/R4/"),
    )
    out = _command("--file", _write(tmp_path, bundle), "--epic")
    by_url = dict(EhrBrand.objects.values_list("fhir_base_url", "vendor__name"))
    assert by_url == {EPIC_SANDBOX_URL: "Epic Sandbox", "https://mercy.example.org/FHIR/R4/": "Epic Production"}
    assert "Skipped 1 sandbox brand(s): Mercy Health Sandbox" in out
    for vendor in EhrVendor.objects.all():
        assert (vendor.skip_type_checklist, vendor.extra_scopes) == (True, "profile")


def test_oracle_health_skips_sandboxes_and_gets_binary_read(db, tmp_path):
    bundle = _brands(
        ("Lakeside Sandbox", "https://fhir.example.com/r4/s/"), ("Lakeside", "https://fhir.example.com/r4/l/")
    )
    _command("--file", _write(tmp_path, bundle), "--oracle-health")
    brand = EhrBrand.objects.get()
    assert (brand.name, brand.vendor.name) == ("Lakeside", "Oracle Health Production")
    assert brand.vendor.extra_scopes == "patient/Binary.read" and brand.vendor.skip_type_checklist is False


def test_a_vendor_without_special_handling_takes_its_settings_from_the_command_line(db, tmp_path):
    path = _write(tmp_path, _brands(("Acme Clinic", "https://acme.example.org/FHIR/R4/")))
    _command("--file", path, "--vendor", "Acme EHR", "--skip-auth-screen", "--extra-scopes", "profile, offline_access")
    vendor = EhrVendor.objects.get(name="Acme EHR")
    assert (vendor.skip_type_checklist, vendor.extra_scopes) == (True, "profile offline_access")


def test_settings_apply_on_create_only_and_a_difference_is_reported(db, tmp_path):
    path = _write(tmp_path, _brands(("Acme Clinic", "https://acme.example.org/FHIR/R4/")))
    EhrVendor.objects.create(name="Acme EHR", extra_scopes="profile")
    out = _command("--file", path, "--vendor", "Acme EHR", "--skip-auth-screen")
    vendor = EhrVendor.objects.get(name="Acme EHR")
    assert (vendor.skip_type_checklist, vendor.extra_scopes) == (False, "profile")
    assert '"Acme EHR" already exists with different sign-in settings' in out

    out = _command("--file", path, "--vendor", "Acme EHR", "--extra-scopes", "profile")
    assert "already exists" not in out  # the same settings: nothing to report


def test_a_known_vendors_row_name_is_refused_with_vendor(db, tmp_path):
    path = _write(tmp_path, _brands(("Acme Clinic", "https://acme.example.org/FHIR/R4/")))
    with pytest.raises(CommandError, match="--epic or --oracle-health"):
        _command("--file", path, "--vendor", "Epic Production")
    assert not EhrBrand.objects.exists()


def test_settings_flags_go_with_vendor_only(db, tmp_path):
    path = _write(tmp_path, _brands(("Acme Clinic", "https://acme.example.org/FHIR/R4/")))
    with pytest.raises(CommandError, match="go with --vendor"):
        _command("--file", path, "--epic", "--extra-scopes", "profile")


@pytest.mark.parametrize(
    "args",
    [("--epic",), ("--file", "x.json"), ("--file", "x.json", "--epic", "--oracle-health")],
    ids=["no file", "no vendor", "two vendors"],
)
def test_the_file_and_exactly_one_vendor_are_required(db, args):
    with pytest.raises(CommandError):
        _command(*args)


def test_a_brand_moved_from_another_vendor_row_is_reported(db, tmp_path):
    path = _write(tmp_path, _brands(("Acme Clinic", "https://acme.example.org/FHIR/R4/")))
    _command("--file", path, "--vendor", "Acme EHR")
    out = _command("--file", path, "--vendor", "Other EHR")
    assert EhrBrand.objects.get().vendor.name == "Other EHR"
    assert "1 brand(s) were under another vendor row" in out
