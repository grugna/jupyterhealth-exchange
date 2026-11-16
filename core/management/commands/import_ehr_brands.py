"""
Import a SMART App Launch 2.2 "user-access Brands Bundle" (FHIR Bundle of
Organization brands + Organization care facilities + Endpoints) into the
EhrBrand / EhrBrandLocation tables that power the Patient Access hospital picker.

The file is always named, and so is whose directory it is:

    python manage.py import_ehr_brands --file epic_brands.json --epic
    python manage.py import_ehr_brands --file oracle_health.json --oracle-health
    python manage.py import_ehr_brands --file other.json --vendor "Some EHR" \
        [--skip-auth-screen] [--extra-scopes scope1,scope2]

A known vendor (--epic, --oracle-health) has its row names and sign-in settings in code
(`KNOWN_VENDORS`, `EhrVendor.DEFAULTS_BY_NAME`). Any other vendor is described on the command
line. Either way the settings are applied when a row is created, never to an existing row: an
operator's edits on the EHRs admin page win, and the import says when a row differs.

Brands whose name contains "sandbox" are test environments and are skipped, except a known
vendor's public sandbox, which is matched by its base URL and filed under its own row (Epic's,
whose client id is JHE's non-production registration).

Epic publishes its bundle at https://open.epic.com/MyApps/Endpoints (User-access Brands Bundle);
that full file is multi-MB and is NOT committed. Oracle Health publishes its patient endpoints in
the same shape at https://github.com/oracle-samples/ignite-endpoints. A small curated Epic sample
ships at core/data/ehr_brands.sample.json for seed/dev.
"""

import json
import os
from dataclasses import dataclass

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import EhrBrand, EhrBrandLocation, EhrVendor

DEFAULT_SAMPLE = os.path.join("core", "data", "ehr_brands.sample.json")


def _first_endpoint_address(org, endpoints):
    """Resolve a brand Organization's first Endpoint reference to its FHIR base URL."""
    for ref in org.get("endpoint", []) or []:
        reference = ref.get("reference", "")
        endpoint = endpoints.get(reference) or endpoints.get(reference.split("/")[-1])
        if endpoint and endpoint.get("address"):
            return endpoint["address"]
    return None


def _local_ref_id(reference):
    """Resolve a FHIR reference to the referenced resource's bare local id.

    Epic's real production bundle links partOf/endpoint via urn:uuid:<id>
    references; the curated sample and other vendors use Organization/<id>.
    Both must resolve to the same id used as the resource's own "id" field.
    """
    if reference.startswith("urn:uuid:"):
        return reference[len("urn:uuid:") :]
    return reference.split("/")[-1]


def _npi_type_2(org):
    for ident in org.get("identifier", []) or []:
        if "us-npi" in (ident.get("system") or ""):
            return ident.get("value")
    return None


def _address_parts(org):
    """Flatten the first address of an Organization into our location columns."""
    addresses = org.get("address", []) or []
    if not addresses:
        return {"address_text": "", "city": None, "state": None, "postal_code": None, "country": None}
    a = addresses[0]
    text = a.get("text")
    if not text:
        text = ", ".join(p for p in [*(a.get("line") or []), a.get("city"), a.get("state"), a.get("postalCode")] if p)
    return {
        "address_text": text or "",
        "city": a.get("city"),
        "state": a.get("state"),
        "postal_code": a.get("postalCode"),
        "country": a.get("country"),
    }


@dataclass(frozen=True)
class KnownVendor:
    """A vendor whose import is defined in code: the row its brands go under, and its public
    sandbox, if any, matched by base URL. The rows' sign-in settings are EhrVendor.defaults_for."""

    vendor_name: str
    sandbox_vendor_name: str | None = None
    sandbox_base_url: str | None = None


KNOWN_VENDORS = {
    "epic": KnownVendor(
        vendor_name=EhrVendor.EPIC_PRODUCTION,
        sandbox_vendor_name=EhrVendor.EPIC_SANDBOX,
        sandbox_base_url="https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4/",
    ),
    "oracle_health": KnownVendor(vendor_name=EhrVendor.ORACLE_HEALTH_PRODUCTION),
}


def _same_url(a, b):
    return a.rstrip("/") == b.rstrip("/")


def _scopes(text):
    """Space-separated scopes, whatever separated them going in (commas, spaces)."""
    return " ".join(dict.fromkeys(text.replace(",", " ").split()))


@transaction.atomic
def import_brands_bundle(bundle, vendor_name, *, settings=None, sandbox_vendor_name=None, sandbox_base_url=None):
    """
    Upsert a Brands Bundle into EhrBrand / EhrBrandLocation. Idempotent: brands
    keyed on fhir_base_url, locations on (brand, name, address_text).

    Every brand goes under the `vendor_name` row, except the brand at `sandbox_base_url`, which
    goes under `sandbox_vendor_name`, and any other brand whose name contains "sandbox", which is
    skipped. A row is created when missing, without a client id, with `settings` (the sign-in
    fields) or, when None, EhrVendor.defaults_for its name; an existing row is left as it is.

    Returns {"brands": n, "locations": m, "skipped": [names], "moved": k, "kept": [row names]}:
    `moved` counts brands that were under another vendor row before, and `kept` names existing
    rows whose sign-in settings differ from what this import would have created them with.
    """
    resources = [e.get("resource", {}) for e in bundle.get("entry", []) or []]
    endpoints = {}
    for entry in bundle.get("entry", []) or []:
        res = entry.get("resource", {})
        if res.get("resourceType") == "Endpoint":
            endpoints[f"Endpoint/{res.get('id')}"] = res
            endpoints[res.get("id")] = res
            if entry.get("fullUrl"):
                endpoints[entry["fullUrl"]] = res

    orgs = [r for r in resources if r.get("resourceType") == "Organization"]
    brand_orgs = [o for o in orgs if o.get("endpoint")]
    facility_orgs = [o for o in orgs if o.get("partOf") and not o.get("endpoint")]

    vendor_cache = {}
    kept = []

    def _vendor(name):
        if name not in vendor_cache:
            wanted = settings if settings is not None and name == vendor_name else EhrVendor.defaults_for(name)
            vendor, created = EhrVendor.objects.get_or_create(name=name, defaults=wanted)
            has = (vendor.skip_type_checklist, _scopes(vendor.extra_scopes))
            if not created and has != (
                wanted.get("skip_type_checklist", False),
                _scopes(wanted.get("extra_scopes", "")),
            ):
                kept.append(name)
            vendor_cache[name] = vendor
        return vendor_cache[name]

    base_urls = [url for url in (_first_endpoint_address(o, endpoints) for o in brand_orgs) if url]
    vendor_before = dict(EhrBrand.objects.filter(fhir_base_url__in=base_urls).values_list("fhir_base_url", "vendor_id"))
    brand_by_org_id = {}  # source Organization id -> EhrBrand
    brand_count = 0
    moved_count = 0
    skipped = []
    for org in brand_orgs:
        base_url = _first_endpoint_address(org, endpoints)
        if not base_url:
            continue
        name = org.get("name") or base_url
        if sandbox_base_url and sandbox_vendor_name and _same_url(base_url, sandbox_base_url):
            vendor = _vendor(sandbox_vendor_name)
        elif "sandbox" in name.lower():
            skipped.append(name)  # a test environment, not a hospital a patient signs in to
            continue
        else:
            vendor = _vendor(vendor_name)
        if vendor_before.get(base_url, vendor.id) != vendor.id:
            moved_count += 1  # brands are keyed on their base URL, so a re-import can move one
        brand, _ = EhrBrand.objects.update_or_create(
            fhir_base_url=base_url,
            defaults={"name": name, "npi_type_2": _npi_type_2(org), "vendor": vendor},
        )
        brand_by_org_id[org.get("id")] = (brand, org)
        brand_count += 1

    location_count = 0
    brands_with_locations = set()
    for org in facility_orgs:
        ref = (org.get("partOf") or {}).get("reference", "")
        brand_entry = brand_by_org_id.get(_local_ref_id(ref))
        if not brand_entry:
            continue
        brand, _ = brand_entry
        parts = _address_parts(org)
        _, created = EhrBrandLocation.objects.get_or_create(
            brand=brand,
            name=org.get("name") or brand.name,
            address_text=parts["address_text"],
            defaults=parts,
        )
        brands_with_locations.add(brand.id)
        if created:
            location_count += 1

    # A brand with no care-facility children is still selectable: seed one
    # location from the brand itself so it shows up in the picker.
    for brand, org in brand_by_org_id.values():
        if brand.id in brands_with_locations:
            continue
        parts = _address_parts(org)
        _, created = EhrBrandLocation.objects.get_or_create(
            brand=brand, name=brand.name, address_text=parts["address_text"], defaults=parts
        )
        if created:
            location_count += 1

    return {
        "brands": brand_count,
        "locations": location_count,
        "skipped": skipped,
        "moved": moved_count,
        "kept": kept,
    }


class Command(BaseCommand):
    help = "Import a SMART user-access Brands Bundle JSON into EhrBrand/EhrBrandLocation."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file", required=True, help=f"Path to a Brands Bundle JSON file (dev sample: {DEFAULT_SAMPLE})."
        )
        whose = parser.add_mutually_exclusive_group(required=True)
        whose.add_argument(
            "--epic",
            action="store_const",
            const="epic",
            dest="known",
            help=f'Epic\'s file: brands under "{EhrVendor.EPIC_PRODUCTION}", its public sandbox under '
            f'"{EhrVendor.EPIC_SANDBOX}", with Epic\'s sign-in settings.',
        )
        whose.add_argument(
            "--oracle-health",
            action="store_const",
            const="oracle_health",
            dest="known",
            help=f'Oracle Health\'s file: brands under "{EhrVendor.ORACLE_HEALTH_PRODUCTION}", with its sign-in settings.',
        )
        whose.add_argument("--vendor", help="Any other vendor: the name of the row to file the brands under.")
        parser.add_argument(
            "--skip-auth-screen",
            action="store_true",
            help="With --vendor: the EHR's own authorization screen asks which record types to share, so JHE "
            "skips its checklist.",
        )
        parser.add_argument(
            "--extra-scopes",
            default="",
            help="With --vendor: comma-separated scopes requested in addition to openid fhirUser launch/patient.",
        )

    def handle(self, *args, **options):
        known = options.get("known")
        if known:
            if options["skip_auth_screen"] or options["extra_scopes"]:
                raise CommandError(
                    "--skip-auth-screen and --extra-scopes go with --vendor; a known vendor's are in code"
                )
            spec = KNOWN_VENDORS[known]
            kwargs = {
                "vendor_name": spec.vendor_name,
                "sandbox_vendor_name": spec.sandbox_vendor_name,
                "sandbox_base_url": spec.sandbox_base_url,
            }
        else:
            vendor_name = options["vendor"].strip()
            if not vendor_name:
                raise CommandError("--vendor needs a vendor row name")
            if vendor_name in EhrVendor.DEFAULTS_BY_NAME:
                raise CommandError(f'"{vendor_name}" is a known vendor\'s row: use --epic or --oracle-health')
            kwargs = {
                "vendor_name": vendor_name,
                "settings": {
                    "skip_type_checklist": options["skip_auth_screen"],
                    "extra_scopes": _scopes(options["extra_scopes"]),
                },
            }
        path = options["file"]
        with open(path, encoding="utf-8") as f:
            bundle = json.load(f)
        counts = import_brands_bundle(bundle, **kwargs)

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {counts['brands']} brand(s), {counts['locations']} location(s) from {path} "
                f'under "{kwargs["vendor_name"]}"'
            )
        )
        if counts["skipped"]:
            self.stdout.write(f"Skipped {len(counts['skipped'])} sandbox brand(s): {', '.join(counts['skipped'])}")
        if counts["moved"]:
            self.stdout.write(
                self.style.WARNING(
                    f"{counts['moved']} brand(s) were under another vendor row and now sign in with this one's "
                    "client id."
                )
            )
        for name in counts["kept"]:
            self.stdout.write(
                self.style.WARNING(
                    f'"{name}" already exists with different sign-in settings; left as it is (change them on the '
                    "EHRs admin page)."
                )
            )
