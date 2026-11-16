"""The library's Directory over JHE's EhrVendor / EhrBrand / EhrBrandLocation rows.

JHE's schema has three levels (vendor, brand with its base URL, location), so the library's
endpoint is the brand and there is no customer level (Simon Q12). Selection ids are location pks,
what the picker already sends. The row carries the deployment's part: its `ehr_client_id` (the
same registration the EHR Patient Portal uses), the scopes its sign-in needs beyond the standard,
and whether the vendor's own screen asks the patient which record types to share. The query table
comes from the library's registry, joined to the row by its display name (PRD §10 question 9: a
`vendor_key` column is the first candidate when vendors are modelled properly). This is the only
module that reads those tables for the library, so a schema change touches it and nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from core.models import EhrBrandLocation, EhrVendor
from patient_access.core.models import Brand, Endpoint, Facility, Selection
from patient_access.core.source_settings import SourceSettings
from patient_access.core.vendors import VENDORS, VendorKey
from patient_access.core.vocab import TypeChoice

# The library's registry key for each vendor row whose query table is not the standard one, matched
# on the row's display name: the existing EhrVendor table has no stable key column (a schema change
# this build does not make; PRD §10 question 9, full-build backlog 8 and 26). Renaming a row in the
# admin unmaps it, and the row is then pulled with the standard table. "SMART Sandbox" is the dev
# seed's vendor; the mapping ships everywhere but only acts where a row of that name exists, which
# also lets a hosted dev server use the SMART sandbox by seeding the row.
VENDOR_KEYS: Mapping[str, VendorKey] = MappingProxyType(
    {
        EhrVendor.EPIC_SANDBOX: VendorKey.EPIC,
        EhrVendor.EPIC_PRODUCTION: VendorKey.EPIC,
        "SMART Sandbox": VendorKey.SMART_REFERENCE,  # dev only: seed_patient_access_sandbox
    }
)


def selection_for(loc: EhrBrandLocation) -> Selection:
    brand = loc.brand
    endpoint = Endpoint(
        id=str(brand.id), iss=brand.fhir_base_url, vendor=brand.vendor.name, fhir_version=brand.fhir_version
    )
    return Selection(
        id=str(loc.id),
        brand=Brand(id=str(brand.id), name=brand.name, endpoint=endpoint, npi=brand.npi_type_2 or None),
        facility=Facility(
            id=str(loc.id),
            name=loc.name,
            address=loc.address_text,
            city=loc.city,
            state=loc.state,
            postal_code=loc.postal_code,
        ),
    )


def _type_choice(row: EhrVendor | None) -> TypeChoice:
    return TypeChoice.EHR if row is not None and row.skip_type_checklist else TypeChoice.HOST


class JheOrmDirectory:
    def selection(self, selection_id: str) -> Selection:
        if not selection_id.isdigit():
            raise KeyError(selection_id)
        loc = EhrBrandLocation.objects.select_related("brand__vendor").filter(id=int(selection_id)).first()
        if loc is None:
            raise KeyError(selection_id)
        return selection_for(loc)

    def settings_for(self, endpoint: Endpoint) -> SourceSettings:
        row = EhrVendor.objects.filter(name=endpoint.vendor).first()
        if row is None or not row.ehr_client_id:
            raise KeyError(endpoint.vendor)
        return VENDORS[VENDOR_KEYS.get(endpoint.vendor, VendorKey.STANDARD)].settings(
            vendor_name=endpoint.vendor,
            client_id=row.ehr_client_id,
            extra_scopes=tuple(row.extra_scopes.split()),
            type_choice=_type_choice(row),
        )

    @staticmethod
    def type_choice(vendor_name: str) -> TypeChoice:
        """Where the patient picks record types for this vendor (the host's checklist unless the
        row says the vendor's own screen asks)."""
        return _type_choice(EhrVendor.objects.filter(name=vendor_name).first())

    @staticmethod
    def type_choices() -> dict[str, TypeChoice]:
        """Every vendor row name, to where its patients pick types."""
        return {
            name: TypeChoice.EHR if skip else TypeChoice.HOST
            for name, skip in EhrVendor.objects.values_list("name", "skip_type_checklist")
        }

    @staticmethod
    def offered_scopes(selection: Selection) -> tuple[str, ...]:
        """The checklist the vendor row offers: what the patient could have chosen."""
        row = EhrVendor.objects.filter(name=selection.endpoint.vendor).first()
        return tuple((row.supported_scopes or "").split()) if row else ()
