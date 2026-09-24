"""结构化日志（JSON）+ traceId 注入 —— 三个服务共用的实现（DEP-10 的地基）。

为什么放在共享层：
    结构化日志是「日志能不能被机器用」的分水岭。若三个服务各写一份 formatter，
    字段名（`ts` vs `time` vs `@timestamp`）与 traceId 的取值方式必然分叉，
    而 DEP-10 的验收是「单次下单可在日志中串起全链路」——字段名不一致时，
    这个断言在 Loki 里根本无法表达。故实现只此一份。

为什么 traceId 从 ContextVar 取而不是随参数传：
    见 `app/core/trace.py`：投递循环、配置订阅这类**没有 request 对象**的上下文
    同样会产生日志，ContextVar 让它们不必层层传参也能带上 traceId。

字段口径（DEP-10 / PRD §10）：
    ts      日志产生时间（UTC，ISO8601，带 Z）
    level   DEBUG/INFO/WARNING/ERROR/CRITICAL
    logger  logger 名（模块路径）
    service 服务名（aids-backend / aids-ai / aids-mock）
    msg     已格式化的人类可读消息
    traceId 当前链路 ID（无上下文时**不输出该键**，而不是输出 null
            —— 后者会让「有 traceId 的日志」与「无上下文的日志」在聚合时混为一谈）
    exc     异常栈（仅在有 exc_info 时出现）
    *       业务方通过 `extra={"fields": {...}}` 附加的自定义结构化字段

用法：
    from app.core.logging import configure_structured_logging, log_fields
    configure_structured_logging("aids-ai")          # 进程启动时调用一次
    logger.info("订单已支付", extra=log_fields(orderNo="ORD1", amount=199.0))
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any, Final

from app.core.trace import current_trace_id

# `configure_structured_logging` 装上的 handler 会带这个标记；
# 幂等实现靠它识别「上次是不是我装的」，而不是粗暴清空 root 的全部 handler
# （清空会把 pytest 的 caplog handler 一起端掉，测试里再也抓不到日志）。
_HANDLER_MARKER: Final[str] = "_aids_structured_handler"

# 业务方附加结构化字段的约定键（见模块 docstring）
FIELDS_EXTRA_KEY: Final[str] = "fields"


class JsonFormatter(logging.Formatter):
    """把 LogRecord 渲染成单行 JSON（便于 Loki/Fluent 采集与按字段检索）。"""

    def __init__(self, service: str, *, ensure_ascii: bool = False) -> None:
        super().__init__()
        self._service = service
        # ensure_ascii=False：中文日志落盘可读；JSON 本身仍是合法 UTF-8
        self._ensure_ascii = ensure_ascii

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self._service,
            "msg": record.getMessage(),
        }

        # 无链路上下文时不写该键（见 docstring 的字段口径）
        trace_id = current_trace_id()
        if trace_id:
            payload["traceId"] = trace_id

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # 异常对象即使没走 exc_info 也可能被塞进 args —— 统一转字符串，
        # 避免 json.dumps 直接抛 TypeError 把「打日志」变成「抛异常」。
        extra = getattr(record, FIELDS_EXTRA_KEY, None)
        if isinstance(extra, dict):
            payload.update(extra)

        return json.dumps(payload, ensure_ascii=self._ensure_ascii, default=str)


def configure_structured_logging(
    service: str,
    level: str | None = None,
    *,
    stream: Any = None,
) -> logging.Handler:
    """把根 logger 换成 JSON 结构化输出。返回装入的 handler。

    **幂等**：重复调用不会叠加 handler（叠加的后果是同一行日志被打两遍，
    在容器日志里表现为"重复告警"，排查时极难与"业务真的执行了两次"区分）。
    实现方式是只摘除**本模块上次装的** handler，不动别的库/测试框架装的。
    """
    resolved = (level or os.getenv("LOG_LEVEL") or "INFO").strip().upper()

    logger = logging.getLogger()
    for existing in list(logger.handlers):
        if getattr(existing, _HANDLER_MARKER, False):
            logger.removeHandler(existing)

    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    setattr(handler, _HANDLER_MARKER, True)
    logger.addHandler(handler)
    logger.setLevel(resolved)
    return handler


def log_fields(**fields: Any) -> dict[str, dict[str, Any]]:
    """构造 `logging` 的 `extra` 参数，附加结构化字段。

    用法：`logger.info("msg", extra=log_fields(orderNo="ORD1"))`。
    """
    return {FIELDS_EXTRA_KEY: fields}
