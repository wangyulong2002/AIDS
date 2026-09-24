"""Mock 短信服务路由（MOCK-02）。

接口（主业务 BE-07 调用）：
    POST /sms/send  {mobile, templateCode, params?, bizNo?}
        - 频控：60s 内同手机号同模板只发一条（idx_mobile_time 的约定）
        - 开发环境回显：响应 data.code 带验证码（API.md §2.1：回显由 MOCK-02 决定）
        - 记录落库（mock_sms_record，状态 1 发送成功 / 2 失败）
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aids_mock.constants import SMS_SENT, SMS_WAITING
from aids_mock.db import get_db
from aids_mock.models import MockSmsRecord
from app.core.errors import CommonError
from app.core.exceptions import BusinessError
from app.core.response import ApiResponse, ok

router = APIRouter(prefix="/sms", tags=["Mock 短信服务"])

MOBILE_PATTERN = re.compile(r"^1[3-9]\d{9}$")
RATE_LIMIT_SECONDS = 60

# 模板编码 → 内容模板（Mock 固定文案；真实渠道由模板 ID 决定）
_TEMPLATES: dict[str, str] = {
    "LOGIN_CODE": "【AIDS】登录验证码 {code}，5 分钟内有效，请勿泄露。",
    "ORDER_SHIP": "【AIDS】您的订单 {orderNo} 已发货，快递单号 {deliveryNo}。",
    "NOTIFY": "【AIDS】{content}",
}


class SmsSendRequest(BaseModel):
    mobile: str = Field(min_length=11, max_length=11)
    templateCode: str = Field(min_length=1)
    params: dict[str, str] = Field(default_factory=dict)
    bizNo: str | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@router.post("/send", summary="发送短信（验证码/通知）")
async def send_sms(
    payload: SmsSendRequest, session: Annotated[AsyncSession, Depends(get_db)]
) -> ApiResponse:
    if not MOBILE_PATTERN.match(payload.mobile):
        raise BusinessError.not_found("手机号格式错误")

    template = _TEMPLATES.get(payload.templateCode)
    if template is None:
        raise BusinessError.not_found(f"未知短信模板：{payload.templateCode}")

    # 频控：60s 内同手机号同模板只发一条（先查后插；Mock 无并发压力，uk 不设）
    recent = (
        await session.execute(
            select(MockSmsRecord)
            .where(
                MockSmsRecord.mobile == payload.mobile,
                MockSmsRecord.template_code == payload.templateCode,
                MockSmsRecord.create_time >= _utcnow() - timedelta(seconds=RATE_LIMIT_SECONDS),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if recent is not None:
        # 10006 → HTTP 429（API.md §1.3 的例外之一）；主业务收到后转 20002 给用户
        raise BusinessError(int(CommonError.RATE_LIMITED), "发送过于频繁，请 60 秒后重试")

    content = template.format(**payload.params)
    record = MockSmsRecord(
        mobile=payload.mobile,
        template_code=payload.templateCode,
        content=content,
        params=payload.params,
        biz_no=payload.bizNo,
        status=SMS_WAITING,
    )
    session.add(record)
    await session.flush()

    # Mock 渠道"发送"即时成功（真实渠道是异步回执）；开发环境把验证码回显给调用方
    record.status = SMS_SENT
    record.send_time = _utcnow()

    dev_code = payload.params.get("code")
    return ok(
        {
            "messageId": str(record.id),
            "status": record.status,
            **({"code": dev_code} if dev_code is not None else {}),
        }
    )


@router.get("/record/{mobile}", summary="查询发送记录（联调/演示用）")
async def list_records(
    mobile: str, session: Annotated[AsyncSession, Depends(get_db)]
) -> ApiResponse:
    """按手机号倒序回最近记录 —— 供联调时核对内容（Mock 库明文仅演示用）。"""
    rows = (
        (
            await session.execute(
                select(MockSmsRecord)
                .where(MockSmsRecord.mobile == mobile)
                .order_by(MockSmsRecord.id.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return ok(
        {
            "list": [
                {
                    "templateCode": r.template_code,
                    "content": r.content,
                    "status": r.status,
                    "sendTime": r.send_time.isoformat() if r.send_time else None,
                }
                for r in rows
            ]
        }
    )
