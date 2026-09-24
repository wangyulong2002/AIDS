"""MOCK-02 短信服务验收测试。

被锁死的语义：
    1. 频控：60s 内同手机号同模板只发一条（重复 → 10006/429，主业务再转 20002 给用户）；
    2. 回显：开发环境把验证码放进响应 `data.code`（API.md §2.1：回显由 MOCK-02 决定）；
    3. 落库：mock_sms_record 状态推进到"发送成功"，内容按模板渲染。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from aids_mock.constants import SMS_SENT
from aids_mock.db import get_db
from aids_mock.models import MockSmsRecord

pytestmark = [pytest.mark.contract, pytest.mark.task("MOCK-02")]


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return self._rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _SmartSession:
    """按 select 的实体分发数据（MockSmsRecord 的近期记录）。"""

    def __init__(self, recent_rows: list[Any] | None = None) -> None:
        self.recent_rows = recent_rows or []
        self.added: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        return _Result(list(self.recent_rows))

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass


def _client(recent_rows: list[Any] | None = None) -> tuple[TestClient, _SmartSession]:
    from aids_mock.app_factory import create_app

    session = _SmartSession(recent_rows)
    app = create_app()
    app.dependency_overrides[get_db] = _fake_db(session)
    return TestClient(app, raise_server_exceptions=False), session


def _fake_db(session: _SmartSession) -> Any:
    async def _yield() -> Any:
        yield cast(AsyncSession, session)

    return _yield


def _body(
    mobile: str = "13800138000", template: str = "LOGIN_CODE", **params: str
) -> dict[str, Any]:
    return {"mobile": mobile, "templateCode": template, "params": params, "bizNo": "BIZ1"}


def test_send_creates_record_and_echoes_code() -> None:
    client, session = _client()
    response = client.post("/sms/send", json=_body(code="123456"))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["code"] == "123456", "开发环境必须回显验证码"
    record = cast(MockSmsRecord, session.added[0])
    assert record.status == SMS_SENT
    assert record.content == "【AIDS】登录验证码 123456，5 分钟内有效，请勿泄露。"


def test_invalid_mobile_rejected() -> None:
    client, _ = _client()
    response = client.post("/sms/send", json=_body(mobile="12345"))
    assert response.json()["code"] != 0


def test_unknown_template_rejected() -> None:
    client, _ = _client()
    response = client.post("/sms/send", json=_body(template="SPAM"))
    assert response.json()["code"] != 0


def test_rate_limit_within_60s() -> None:
    """频控：60s 内同手机号同模板重复发送 → 10006（HTTP 429）。"""
    recent = SimpleNamespace(id=1, mobile="13800138000", template_code="LOGIN_CODE")
    client, session = _client(recent_rows=[recent])
    response = client.post("/sms/send", json=_body())
    assert response.status_code == 429
    assert response.json()["code"] == 10006
    assert session.added == [], "被频控的请求不得产生新的发送记录"


def test_records_endpoint_returns_history() -> None:
    history = SimpleNamespace(
        template_code="LOGIN_CODE", content="【AIDS】登录验证码 123456", status=1, send_time=None
    )
    client, session = _client()
    session.recent_rows = [history]

    response = client.get("/sms/record/13800138000")
    assert response.status_code == 200
    assert response.json()["data"]["list"][0]["content"].startswith("【AIDS】")
