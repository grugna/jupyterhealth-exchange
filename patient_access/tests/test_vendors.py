"""The vendor registry: what is the same in every deployment."""

from patient_access.core.source_settings import BASE_SCOPES
from patient_access.core.vendors import VENDORS, TypeChoice, VendorKey


def test_a_vendor_without_an_entry_of_its_own_gets_the_standard_table():
    assert VENDORS[VendorKey.STANDARD].fetch.name == "uscdi"


def test_the_host_adds_scopes_to_the_base_set_and_sets_the_type_choice():
    settings = VENDORS[VendorKey.EPIC].settings(
        vendor_name="Epic Sandbox", client_id="c", extra_scopes=("profile", "openid"), type_choice=TypeChoice.EHR
    )
    assert settings.registration.base_scopes == (*BASE_SCOPES, "profile")  # never fewer, never twice
    assert settings.type_choice is TypeChoice.EHR


def test_by_default_a_vendor_gets_the_base_scopes_and_our_checklist():
    settings = VENDORS[VendorKey.STANDARD].settings(vendor_name="Some EHR", client_id="c")
    assert settings.registration.base_scopes == BASE_SCOPES and settings.type_choice is TypeChoice.HOST


def test_every_vendor_settings_carries_its_registration_and_profile():
    for key, vendor in VENDORS.items():
        settings = vendor.settings(vendor_name=f"{key} Sandbox", client_id="c")
        assert settings.registration.client_id == "c"
        assert settings.fetch is vendor.fetch
