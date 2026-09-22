"""业务异常（跨服务共享）。

为什么放在共享层，而不是三个服务各写一份：
    统一响应体（`app/core/response.py`）与错误码表（`app/core/errors.py`）都是
    跨服务契约——主业务 / AI / Mock 三个服务必须抛出**同一种**异常、走**同一套**
    「异常 → {code,message,data}」映射。若各服务自定义异常类，就会出现
    「同样的错误在不同服务返回不同结构」，前端拦截器疲于适配。
    这正是 bysj 的老问题，故在共享层收口。

与 HTTP 状态码的关系（API.md §1.3）：
    错误码表已声明每个码对应的 HTTP 状态，且**只有 5 个例外**：
        10002 → 401   10003 → 403   10005 → 403   10006 → 429   10008 → 500
    其余业务码一律 200（「业务失败也返回 200，用 code 区分」）。
    那张表在 `app/core/response.py::HTTP_STATUS_EXCEPTIONS` 单一定义，
    本模块只做查询，**不复制、不另起一套**。

由 `tests/api/test_app_skeleton.py` 断言映射行为。
"""

from __future__ import annotations

from typing import Any

from app.core.errors import CommonError
from app.core.response import HTTP_STATUS_EXCEPTIONS


class BusinessError(Exception):
    """可预期的业务失败。

    业务代码**只抛这个类**：
        - 不要 `return {"code": 40002, ...}` —— 绕过统一响应体
        - 不要 `raise HTTPException(400)` —— 同上，且会被 FastAPI 默认处理器接管
    错误码必须来自 `app/core/errors.py` 的枚举，禁止裸数字
    （由 `tests/contract/scan_error_codes.py` 在 pre-commit 与 CI 中强制）。
    """

    def __init__(
        self,
        code: int,
        message: str,
        data: Any = None,
        http_status: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.data = data
        # 仅极少数场景需要显式覆盖（如第三方回调要求固定状态码）
        self.http_status = http_status
        super().__init__(f"[{code}] {message}")

    @property
    def resolved_http_status(self) -> int:
        """本次异常最终返回的 HTTP 状态码。

        显式指定优先；否则查 `HTTP_STATUS_EXCEPTIONS`；
        未登记的业务码一律 200 —— 这是前端 axios 拦截器的契约，不能擅自改。
        """
        if self.http_status is not None:
            return self.http_status
        return HTTP_STATUS_EXCEPTIONS.get(self.code, 200)

    # ------------------------------------------------------------------
    # 高频错误的便捷构造（等价于 cls(int(XxxError.YYY), msg)，只是少写一层）
    # ------------------------------------------------------------------

    @classmethod
    def not_found(cls, message: str = "资源不存在") -> BusinessError:
        return cls(int(CommonError.NOT_FOUND), message)

    @classmethod
    def param_invalid(cls, message: str = "参数校验失败") -> BusinessError:
        return cls(int(CommonError.PARAM_INVALID), message)

    @classmethod
    def unauthorized(cls, message: str = "未登录或 Token 失效") -> BusinessError:
        return cls(int(CommonError.UNAUTHORIZED), message)

    @classmethod
    def forbidden(cls, message: str = "无权限访问") -> BusinessError:
        return cls(int(CommonError.FORBIDDEN), message)

    @classmethod
    def data_forbidden(cls, message: str = "无权访问该数据") -> BusinessError:
        """IDOR 拦截（S4，PRD §2.2）。

        注意 message **不能暴露资源是否存在**——「无权访问该数据」而不是
        「你不是该订单的拥有者」，否则攻击者可用 403/404 的差异枚举他人资源。
        """
        return cls(int(CommonError.DATA_FORBIDDEN), message)
