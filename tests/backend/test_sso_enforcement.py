"""SSO-only sign-in for practitioners/staff (auth.sso.required_for_practitioners) and the
network allowlist for /admin/ (auth.admin.allowed_cidrs)."""

import ipaddress

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.urls import reverse

from core.middleware import client_ip
from core.models import JheSetting, JheUser
from core.services.jhe_settings import validate_setting


def set_setting(key, value_type, value):
    setting, _ = JheSetting.objects.update_or_create(key=key, defaults={"value_type": value_type})
    setting.set_value(value_type, value)
    setting.save()
    cache.clear()


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def make(email, **kw):
    return JheUser.objects.create_user(email=email, password="testpass123", **kw)


def login(client, email):
    return client.post(reverse("login"), {"username": email, "password": "testpass123"})


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kw",
    [
        {"user_type": "practitioner"},
        {"user_type": "practitioner", "is_staff": True},
        {"user_type": "practitioner", "is_staff": True, "is_superuser": True},
    ],
)
def test_password_login_refused_for_practitioners_when_enforced(client, kw):
    make("p@example.org", **kw)
    set_setting("auth.sso.required_for_practitioners", "bool", True)
    response = login(client, "p@example.org")
    assert response.status_code == 200 and b"Continue with SAML2" in response.content
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_password_login_works_when_not_enforced(client):
    make("p@example.org", user_type="practitioner")
    response = login(client, "p@example.org")
    assert response.status_code == 302 and "_auth_user_id" in client.session


@pytest.mark.django_db
def test_patients_are_not_affected(client):
    make("pt@example.org", user_type="patient")
    set_setting("auth.sso.required_for_practitioners", "bool", True)
    login(client, "pt@example.org")
    assert "_auth_user_id" in client.session


def _admin_get(client, ip, xff=None):
    extra = {"REMOTE_ADDR": "10.0.0.9"}
    if xff is not None:
        extra["HTTP_X_FORWARDED_FOR"] = xff
    return client.get("/admin/login/", **extra)


@pytest.mark.django_db
def test_admin_open_when_allowlist_empty(client):
    assert _admin_get(client, None, "8.8.8.8").status_code == 200


@pytest.mark.django_db
def test_admin_allowlist_admits_listed_networks_only(client):
    set_setting("auth.admin.allowed_cidrs", "string", "203.0.113.0/24, 198.51.100.7")
    assert _admin_get(client, None, "203.0.113.50").status_code == 200
    assert _admin_get(client, None, "198.51.100.7").status_code == 200
    assert _admin_get(client, None, "8.8.8.8").status_code == 403


@pytest.mark.django_db
def test_admin_allowlist_ignores_forged_forwarded_entries(client):
    set_setting("auth.admin.allowed_cidrs", "string", "203.0.113.0/24")
    # The client claims a listed address, but the load balancer appended the real one last.
    assert _admin_get(client, None, "203.0.113.5, 8.8.8.8").status_code == 403
    assert _admin_get(client, None, "8.8.8.8, 203.0.113.5").status_code == 200


@pytest.mark.django_db
def test_allowlist_covers_only_admin(client):
    set_setting("auth.admin.allowed_cidrs", "string", "203.0.113.0/24")
    assert client.get("/health", REMOTE_ADDR="8.8.8.8").status_code == 200
    assert client.get(reverse("login"), HTTP_X_FORWARDED_FOR="8.8.8.8").status_code == 200


def test_client_ip_without_header_is_the_peer():
    request = RequestFactory().get("/", REMOTE_ADDR="192.0.2.1")
    assert client_ip(request) == ipaddress.ip_address("192.0.2.1")
    assert client_ip(RequestFactory().get("/", REMOTE_ADDR="nonsense")) is None


def test_cidr_setting_is_validated():
    validate_setting("auth.admin.allowed_cidrs", "string", "")
    validate_setting("auth.admin.allowed_cidrs", "string", "10.0.0.0/8 2001:db8::/32")
    with pytest.raises(ValidationError):
        validate_setting("auth.admin.allowed_cidrs", "string", "10.0.0.0/8, nonsense")


@pytest.mark.django_db
def test_otp_login_refused_for_practitioners_when_enforced(client):
    from allauth.account.adapter import get_adapter
    from django.contrib.messages.storage.fallback import FallbackStorage

    user = make("p@example.org", user_type="practitioner")
    set_setting("auth.sso.required_for_practitioners", "bool", True)
    request = RequestFactory().get("/")
    request.session = {}
    request._messages = FallbackStorage(request)
    kw = dict(email_verification="none", email=None, signup=False, redirect_url=None)
    refused = get_adapter().pre_login(request, user, signal_kwargs=None, **kw)
    assert refused.status_code == 302
    sso = get_adapter().pre_login(request, user, signal_kwargs={"sociallogin": object()}, **kw)
    assert sso is None


@pytest.mark.django_db
def test_practitioner_signup_refused_when_enforced(client):
    set_setting("auth.sso.required_for_practitioners", "bool", True)
    before = JheUser.objects.count()
    client.post(reverse("signup"), {"email": "n@example.org", "password": "Passw0rd!x1", "user_type": "practitioner"})
    assert JheUser.objects.count() == before
