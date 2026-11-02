"""Uploads, regex rules, statement logging, re-authentication and 2FA storage."""
import logging
import time
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi_users.jwt import generate_jwt
from httpx import AsyncClient
from sqlalchemy import text

from app.core.auth import get_jwt_strategy
from app.core.config import get_settings
from app.core.rate_limit import RateLimiter
from app.models.transaction_attachment import TransactionAttachment
from app.services.rule_engine import _match_condition

STORAGE_PATCH = "app.services.attachment_service.get_storage_provider"
HTML = b"<!doctype html><script>alert(1)</script>"
TINY_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"


@dataclass
class _Stored:
    storage_key: str
    size: int
    content_type: str


def _storage(download: bytes = HTML) -> AsyncMock:
    mock = AsyncMock()
    mock.upload.side_effect = lambda key, data, ct: _Stored(key, len(data), ct)
    mock.download.return_value = download
    return mock


async def _upload(client, headers, tx_id, name, content_type, data):
    return await client.post(
        f"/api/transactions/{tx_id}/attachments",
        files={"file": (name, data, content_type)},
        headers=headers,
    )


@pytest.mark.asyncio
async def test_upload_ignores_the_claimed_type_and_reads_the_bytes(
    client: AsyncClient, auth_headers, test_transactions
):
    tx_id = test_transactions[0].id
    with patch(STORAGE_PATCH, return_value=_storage()):
        fake = await _upload(client, auth_headers, tx_id, "a.png", "text/html", HTML)
        real = await _upload(client, auth_headers, tx_id, "b.png", "text/html", TINY_PNG)
    assert fake.status_code == 201
    assert fake.json()["content_type"] == "application/octet-stream"
    assert real.json()["content_type"] == "image/png"


@pytest.mark.asyncio
async def test_a_stored_html_attachment_downloads_as_inert_bytes(
    client: AsyncClient, session, auth_headers, test_transactions, test_workspace, test_user
):
    tx = test_transactions[0]
    row = TransactionAttachment(
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        transaction_id=tx.id,
        filename="old.png",
        storage_key="k",
        content_type="text/html",
        size=len(HTML),
    )
    session.add(row)
    await session.commit()

    with patch(STORAGE_PATCH, return_value=_storage()):
        response = await client.get(
            f"/api/transactions/{tx.id}/attachments/{row.id}", headers=auth_headers
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_a_real_image_still_previews_inline(
    client: AsyncClient, auth_headers, test_transactions
):
    tx_id = test_transactions[0].id
    with patch(STORAGE_PATCH, return_value=_storage(TINY_PNG)):
        made = await _upload(client, auth_headers, tx_id, "b.png", "image/png", TINY_PNG)
        response = await client.get(
            f"/api/transactions/{tx_id}/attachments/{made.json()['id']}", headers=auth_headers
        )
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["content-disposition"].startswith("inline")
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_an_attachment_is_reached_only_through_its_own_transaction(
    client: AsyncClient, auth_headers, test_transactions
):
    first, other = test_transactions[0].id, test_transactions[1].id
    with patch(STORAGE_PATCH, return_value=_storage(TINY_PNG)):
        made = await _upload(client, auth_headers, first, "b.png", "image/png", TINY_PNG)
        attachment_id = made.json()["id"]
        url = f"/api/transactions/{other}/attachments/{attachment_id}"
        assert (await client.get(url, headers=auth_headers)).status_code == 404
        renamed = await client.patch(url, json={"filename": "x.png"}, headers=auth_headers)
        assert renamed.status_code == 404
        assert (await client.delete(url, headers=auth_headers)).status_code == 404


def _fake_tx(**fields) -> Any:
    return cast(Any, SimpleNamespace(**fields))


def test_a_catastrophic_regex_gives_up_instead_of_hanging():
    tx = _fake_tx(description="A" * 64 + "!")
    condition = {"field": "description", "op": "regex", "value": "(A+)+$"}
    started = time.monotonic()
    assert _match_condition(condition, tx) is False
    assert time.monotonic() - started < 5


def test_ordinary_regex_rules_keep_matching():
    tx = _fake_tx(description="Café Example 123")
    assert _match_condition({"field": "description", "op": "regex", "value": r"cafe\s+\w+"}, tx)
    assert not _match_condition({"field": "description", "op": "regex", "value": r"^example"}, tx)


@pytest.mark.parametrize("op", ["gt", "gte", "lt", "lte"])
def test_an_unreadable_number_in_a_rule_matches_nothing(op):
    tx = _fake_tx(amount="10.00")
    assert _match_condition({"field": "amount", "op": op, "value": "abc"}, tx) is False


def test_numeric_rule_conditions_still_compare():
    tx = _fake_tx(amount="10.00")
    assert _match_condition({"field": "amount", "op": "gt", "value": "5"}, tx) is True
    assert _match_condition({"field": "amount", "op": "lt", "value": "5"}, tx) is False


@pytest.mark.asyncio
async def test_a_failed_import_does_not_log_the_file_contents(
    client: AsyncClient, auth_headers, caplog
):
    marker = b"INVENTED-STATEMENT-LINE"
    with (
        caplog.at_level(logging.ERROR),
        patch("app.services.import_service.parse_csv", side_effect=RuntimeError("boom")),
    ):
        response = await client.post(
            "/api/transactions/import/preview",
            files={"file": ("a.csv", marker, "text/csv")},
            headers=auth_headers,
        )
    assert response.status_code == 400
    assert "INVENTED-STATEMENT-LINE" not in caplog.text


def _token_issued_ago(user, seconds: int) -> str:
    """A still valid token whose issue time is `seconds` in the past."""
    lifetime = get_settings().access_token_expire_minutes * 60
    return generate_jwt(
        {"sub": str(user.id), "aud": ["fastapi-users:auth"]},
        get_settings().secret_key.get_secret_value(),
        lifetime - seconds,
    )


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,url,body",
    [
        ("post", "/api/auth/2fa/setup", None),
        ("post", "/api/auth/passkeys/register/options", {"name": "Key"}),
        ("patch", "/api/users/me", {"password": "another-invented-pass"}),
        ("patch", "/api/users/me", {"email": "new@example.com"}),
    ],
)
async def test_sensitive_account_changes_need_a_recent_login(
    client: AsyncClient, test_user, method, url, body
):
    stale = _bearer(_token_issued_ago(test_user, 3600))
    response = await getattr(client, method)(url, headers=stale, **({"json": body} if body else {}))
    assert response.status_code == 403
    assert response.json()["detail"] == "REAUTH_REQUIRED"


@pytest.mark.asyncio
async def test_deleting_a_passkey_needs_a_recent_login(client: AsyncClient, test_user):
    stale = _bearer(_token_issued_ago(test_user, 3600))
    response = await client.delete(f"/api/auth/passkeys/{uuid.uuid4()}", headers=stale)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_stale_token_can_still_change_ordinary_preferences(client: AsyncClient, test_user):
    stale = _bearer(_token_issued_ago(test_user, 3600))
    response = await client.patch("/api/users/me", json={"preferences": {"x": 1}}, headers=stale)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_a_fresh_login_may_change_them(client: AsyncClient, test_user):
    fresh = _bearer(await get_jwt_strategy().write_token(test_user))
    response = await client.post("/api/auth/2fa/setup", headers=fresh)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_setup_cannot_replace_a_secret_while_2fa_is_on(
    client: AsyncClient, session, test_user_with_2fa
):
    before = test_user_with_2fa.totp_secret
    fresh = _bearer(await get_jwt_strategy().write_token(test_user_with_2fa))
    response = await client.post("/api/auth/2fa/setup", headers=fresh)
    assert response.status_code == 400
    await session.refresh(test_user_with_2fa)
    assert test_user_with_2fa.totp_secret == before


@pytest.mark.asyncio
async def test_the_totp_secret_is_encrypted_in_the_database(
    client: AsyncClient, session, test_user
):
    fresh = _bearer(await get_jwt_strategy().write_token(test_user))
    secret = (await client.post("/api/auth/2fa/setup", headers=fresh)).json()["secret"]
    stored = await session.scalar(
        text("select totp_secret from users where email = :email"), {"email": test_user.email}
    )
    assert stored and secret not in stored
    await session.refresh(test_user)
    assert test_user.totp_secret == secret


@pytest.mark.asyncio
async def test_2fa_on_with_an_unreadable_secret_still_challenges(
    client: AsyncClient, session, test_user_with_2fa
):
    """A rotated SECRET_KEY must lock the door, not open it."""
    await session.execute(
        text("update users set totp_secret = 'unreadable' where id = :id"),
        {"id": test_user_with_2fa.id.hex},
    )
    await session.commit()
    response = await client.post(
        "/api/auth/login",
        data={"username": test_user_with_2fa.email, "password": "testpass123"},
    )
    assert "access_token" not in response.json()


def _limiter_redis(count: int, oldest: float):
    pipe = AsyncMock()
    pipe.zremrangebyscore = lambda *a: pipe
    pipe.zcard = lambda *a: pipe
    pipe.zrange = lambda *a, **k: pipe
    pipe.zadd = lambda *a: pipe
    pipe.expire = lambda *a: pipe
    pipe.execute = AsyncMock(return_value=[0, count, [("k", oldest)]])
    redis = AsyncMock()
    redis.pipeline = lambda: pipe
    return redis, pipe


@pytest.mark.asyncio
async def test_a_rejected_attempt_does_not_extend_the_lockout():
    limiter = RateLimiter(max_requests=5, window_seconds=60)
    now = time.time()
    redis, pipe = _limiter_redis(5, now - 50)
    added = []
    pipe.zadd = lambda *a: added.append(a) or pipe
    request: Any = SimpleNamespace(
        url=SimpleNamespace(path="/x"), headers={}, client=SimpleNamespace(host="192.0.2.1")
    )
    with patch("app.core.rate_limit.get_redis", AsyncMock(return_value=redis)):
        with pytest.raises(HTTPException) as caught:
            await limiter(request)
    assert caught.value.status_code == 429
    assert added == []
    assert 1 <= int((caught.value.headers or {})["Retry-After"]) <= 11


@pytest.mark.asyncio
async def test_a_failed_sync_does_not_return_the_exception_text(
    client: AsyncClient, auth_headers, test_connection
):
    with patch(
        "app.services.connection_service.sync_connection",
        side_effect=RuntimeError("invented-provider-detail"),
    ):
        response = await client.post(
            f"/api/connections/{test_connection.id}/sync", headers=auth_headers
        )
    assert response.status_code == 500
    assert "invented-provider-detail" not in response.text
