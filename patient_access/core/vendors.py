"""The vendor registry: the query table each EHR vendor is pulled with, the same in every deployment.

A deployment's part is its client id per vendor and environment, plus what that vendor's sign-in
needs beyond the standard (scopes added to the base set, and whether the vendor's own screen asks
the patient which record types to share). The host's directory supplies both (JHE: the
`EhrVendor` row). An entry here is only for a vendor whose query table differs: a profile in
`profiles.py` and tests. Any other vendor is pulled with `STANDARD`.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from .credentials import PUBLIC, AuthMethod
from .models import Value
from .profiles import EPIC_PROFILE, SMART_REFERENCE_PROFILE, USCDI_PROFILE
from .source_settings import BASE_SCOPES, ClientRegistration, FetchProfile, SourceSettings
from .vocab import ScopeVersion, TypeChoice


class VendorKey(StrEnum):
    STANDARD = "standard"  # any vendor without an entry of its own: the USCDI table as published
    EPIC = "epic"
    SMART_REFERENCE = "smart-reference"  # the SMART Health IT sandbox: dev and end-to-end runs


class Vendor(Value):
    key: VendorKey
    fetch: FetchProfile
    scope_version: ScopeVersion = ScopeVersion.V1
    methods: tuple[AuthMethod, ...] = (PUBLIC,)  # what this build registers: public clients

    def settings(
        self,
        *,
        vendor_name: str,
        client_id: str,
        extra_scopes: tuple[str, ...] = (),
        type_choice: TypeChoice = TypeChoice.HOST,
    ) -> SourceSettings:
        """This vendor's settings for one registration (the host's vendor name, client id, and
        what the host knows the vendor's sign-in needs)."""
        return SourceSettings(
            registration=ClientRegistration(
                vendor=vendor_name,
                client_id=client_id,
                methods=self.methods,
                base_scopes=tuple(dict.fromkeys((*BASE_SCOPES, *extra_scopes))),
                scope_version=self.scope_version,
            ),
            fetch=self.fetch,
            type_choice=type_choice,
        )


VENDORS: Mapping[VendorKey, Vendor] = MappingProxyType(
    {
        VendorKey.STANDARD: Vendor(key=VendorKey.STANDARD, fetch=USCDI_PROFILE),
        VendorKey.EPIC: Vendor(key=VendorKey.EPIC, fetch=EPIC_PROFILE),
        VendorKey.SMART_REFERENCE: Vendor(key=VendorKey.SMART_REFERENCE, fetch=SMART_REFERENCE_PROFILE),
    }
)
