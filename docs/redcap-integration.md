# Integrating REDCap: patient invitations created from a data entry trigger

An external system (here REDCap's *Data Entry Trigger*) can create a JHE patient and get the
link the patient uses to share their EHR data, using only JHE's public API and a practitioner
API key. JHE needs no special endpoint for this. This page is the JHE side of it; the AWS
deployment of the receiving Lambda is documented in
[jupyterhealth-exchange-aws](https://github.com/the-commons-project/jupyterhealth-exchange-aws)
(`docs/redcap-integration.md`).

## The integration account

Use a dedicated practitioner as a service account, never a person's account:

1. Create the user (Django admin: **Jhe users**, then a **Practitioner** profile). Set **User
   type** to `practitioner` when you create it. Since this branch, creating a practitioner
   profile fills an empty `user_type` automatically; before that, a profile made in the admin
   left it empty and the organization's member list (which filters on `user_type`) hid the user.
2. Add it to the organization the patients should join, with the role **member**. `member`
   holds `patient.manage_for_organization`, which creating a patient needs; `viewer` does not.
3. Create its API key. `POST /api/v1/practitioner_clients` creates a key only for the signed-in
   practitioner, and a service account that signs in through SSO cannot sign in to do it, so
   create the OAuth application directly: a **confidential** application with the
   **client credentials** grant, owned by the service account, with `hash_client_secret` off,
   and a `PractitionerClient` row linking it to the practitioner. The key is
   `base64(client_id + ":" + client_secret)`.
4. If `auth.practitioner_clients.allowed_emails` is set, the service account's e-mail must be
   in it, or the key's token request is refused.

## The calls

All JSON; `Authorization: Bearer <access token>` after the first call.

| step | call | notes |
|---|---|---|
| token | `POST /o/token/`, header `Authorization: Basic <key>`, form `grant_type=client_credentials` | the token lasts two weeks (`ACCESS_TOKEN_EXPIRE_SECONDS`); the integration asks for a fresh one per run |
| the client | `GET /api/v1/clients` | find the patient client by `name`; its `id` is the **database id**, not the `clientId` string |
| existing patient? | `GET /api/v1/patients/global_lookup?email=<e-mail>` | a list; empty when there is none |
| in the organization? | `PATCH /api/v1/patients/<id>/global_add_organization?organization_id=<n>` | only when the patient exists and is in another organization |
| create | `POST /api/v1/patients` with `{"organization_id": n, "telecom_email": "<e-mail>"}` | `organization_id` links the patient to the organization at creation; the caller needs `patient.manage_for_organization` in it |
| link | `POST /api/v1/invitation` with `{"patient_id": id, "client_id": <client database id>}` | the answer carries `invitationLink` (and `token`); the client must have an invitation URL |

An invitation is issued for the link's lifetime (`auth.patient.invitation_expiration_days`,
default 7) and is single-use; the patient redeems it by opening the link.

## Things to know

- **Invitation creation does not check the caller's role or organization.** Any signed-in user
  can create an invitation for any patient id. The `member` role therefore limits what the
  service account can do to *patients* (create, look up, add to an organization), not to
  invitations. Treat its key as able to invite any patient, and keep the account's role and
  organization as narrow as the integration allows.
- **Keys bypass SAML and the network allowlist.** A key is a client-credentials grant: it does
  not sign in through the SAML button, and `auth.admin.allowed_cidrs` limits only `/admin/` and the
  settings API, not `/o/token/` or `/api/v1/*`.
- **The patient's e-mail is the only identity** this flow supplies. Names, birth date and
  identifiers can be added with the same `POST /api/v1/patients` body (`name_family`,
  `name_given`, `birth_date`, `identifiers`) if the source system holds them.
- **No mail is sent by this flow.** The link is returned to the caller (`send_email` is not set),
  so the receiving system decides how the patient gets it.
