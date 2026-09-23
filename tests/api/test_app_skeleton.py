"""BE-01 契约测试：应用骨架与统一响应。

校验链：
    ``app/core/response.py``（统一响应 SSOT）←→ ``aids_backend/handlers.py``（异常收敛）

为什么异常路径必须单独测：
    正常返回走 response_model，结构自然正确；而**异常**最容易被漏掉——
    FastAPI 默认处理器会把它接管成 ``{"detail": ...}``。
    前端 axios 拦截器拿到 detail 结构只会报「未知错误」，且这类问题只在出错时
    才暴露，手工很难覆盖全，故在此逐分支固化。

响应体约定见 docs/API.md §1.1；HTTP 状态码例外见 §1.3 与
``app/core/response.py::HTTP_STATUS_EXCEPTIONS``。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from aids_backend.api import MODULE_ROUTERS
from aids_backend.app_factory import create_app
from aids_backend.handlers import register_exception_handlers
from app.core.errors import SUCCESS, CommonError, ProductError
from app.core.exceptions import BusinessError
from app.core.response import HTTP_STATUS_EXCEPTIONS

pytestmark = [pytest.mark.contract, pytest.mark.task("BE-01")]

# 必须定义在模块级：文件顶部有 `from __future__ import annotations`，
# 注解会变成字符串，FastAPI 用 get_type_hints 在**模块全局**里解析——
# 定义在函数内的模型解析不到，路由注册会直接失败。
ENVELOPE_KEYS = {"code", "message", "data"}


class ProbePayload(BaseModel):
    """探针请求体：用于触发参数校验失败。"""

    quantity: int = Field(ge=1)


@pytest.fixture()
def live_client() -> TestClient:
    """真实应用（create_app）的测试客户端。"""
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture()
def probe_client() -> TestClient:
    """挂探针路由的测试客户端，覆盖各类异常分支。

    为什么不在生产 app 上挂探针路由：它们会出现在 /docs 与 openapi.json 里，
    等于给生产环境凭空加接口。这里单独装配一个只含异常处理器的 app。
    """
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/probe/business")
    async def _probe_business() -> None:
        # 30003 不在 HTTP_STATUS_EXCEPTIONS 中 → 应返回 HTTP 200
        raise BusinessError(int(ProductError.STOCK_INSUFFICIENT), "库存不足")

    @app.get("/probe/unauthorized")
    async def _probe_unauthorized() -> None:
        raise BusinessError.unauthorized()

    @app.get("/probe/data-forbidden")
    async def _probe_data_forbidden() -> None:
        raise BusinessError.data_forbidden()

    @app.get("/probe/override-status")
    async def _probe_override() -> None:
        raise BusinessError(int(ProductError.STOCK_INSUFFICIENT), "库存不足", http_status=409)

    @app.get("/probe/boom")
    async def _probe_boom() -> None:
        raise RuntimeError("内部细节：连接串 mysql://root:secret@db/aids_shop 不应外泄")

    @app.post("/probe/validate")
    async def _probe_validate(payload: ProbePayload) -> dict[str, int]:
        return {"quantity": payload.quantity}

    return TestClient(app, raise_server_exceptions=False)


# =====================================================================
# 一、统一响应体结构（API.md §1.1）
# =====================================================================


class TestEnvelopeShape:
    def test_health_returns_three_keys(self, live_client: TestClient) -> None:
        resp = live_client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == ENVELOPE_KEYS, f"响应体字段漂移：{sorted(body)}"

    def test_health_code_is_success(self, live_client: TestClient) -> None:
        assert live_client.get("/health").json()["code"] == SUCCESS

    def test_health_body_reports_up(self, live_client: TestClient) -> None:
        data = live_client.get("/health").json()["data"]
        assert data["status"] == "up"
        assert "env" in data

    def test_health_does_not_leak_internals(self, live_client: TestClient) -> None:
        """探针是未鉴权接口，不应暴露版本号/连接串等内部信息。"""
        data = live_client.get("/health").json()["data"]
        assert set(data) == {"status", "env"}, f"探针泄漏了额外字段：{sorted(data)}"


# =====================================================================
# 二、业务异常 → HTTP 状态码映射（API.md §1.3）
# =====================================================================


class TestBusinessErrorStatusMapping:
    """HTTP_STATUS_EXCEPTIONS 是这张映射的唯一来源，此处只验证行为一致。"""

    @pytest.mark.parametrize(
        ("code", "expected_status"),
        [(10002, 401), (10003, 403), (10005, 403), (10006, 429), (10008, 500)],
    )
    def test_exception_codes_map_to_declared_status(self, code: int, expected_status: int) -> None:
        exc = BusinessError(code, "x")
        assert exc.resolved_http_status == expected_status
        assert HTTP_STATUS_EXCEPTIONS[code] == expected_status

    @pytest.mark.parametrize("code", [10001, 10004, 10007, 10009, 30003, 40001, 70001])
    def test_other_business_codes_stay_200(self, code: int) -> None:
        """核心约定：业务失败也返回 HTTP 200，用 code 区分。"""
        assert BusinessError(code, "x").resolved_http_status == 200

    def test_explicit_override_wins(self) -> None:
        assert (
            BusinessError(
                int(ProductError.STOCK_INSUFFICIENT), "x", http_status=409
            ).resolved_http_status
            == 409
        )

    def test_convenience_constructors_use_enum_codes(self) -> None:
        assert BusinessError.not_found().code == int(CommonError.NOT_FOUND)
        assert BusinessError.param_invalid().code == int(CommonError.PARAM_INVALID)
        assert BusinessError.unauthorized().code == int(CommonError.UNAUTHORIZED)
        assert BusinessError.forbidden().code == int(CommonError.FORBIDDEN)
        assert BusinessError.data_forbidden().code == int(CommonError.DATA_FORBIDDEN)


# =====================================================================
# 三、异常处理器（响应体 + 状态码都不可漂移）
# =====================================================================


class TestExceptionHandlers:
    def test_business_error_returns_200_with_code(self, probe_client: TestClient) -> None:
        resp = probe_client.get("/probe/business")
        assert resp.status_code == 200, "业务失败必须是 200（API.md §1.1）"
        assert resp.json() == {
            "code": int(ProductError.STOCK_INSUFFICIENT),
            "message": "库存不足",
            "data": None,
        }

    def test_unauthorized_maps_to_401(self, probe_client: TestClient) -> None:
        resp = probe_client.get("/probe/unauthorized")
        assert resp.status_code == 401
        assert resp.json()["code"] == int(CommonError.UNAUTHORIZED)

    def test_idor_maps_to_403(self, probe_client: TestClient) -> None:
        resp = probe_client.get("/probe/data-forbidden")
        assert resp.status_code == 403
        assert resp.json()["code"] == int(CommonError.DATA_FORBIDDEN)

    def test_idor_message_does_not_reveal_existence(self, probe_client: TestClient) -> None:
        """IDOR 提示语不得暴露资源是否存在（否则可被用于枚举）。"""
        message = probe_client.get("/probe/data-forbidden").json()["message"]
        for leak in ("不存在", "不属于你", "无此", "已被删除"):
            assert leak not in message, f"IDOR 提示语泄漏了资源存在性：{message}"

    def test_explicit_http_status_override(self, probe_client: TestClient) -> None:
        assert probe_client.get("/probe/override-status").status_code == 409

    def test_validation_error_returns_10001_with_200(self, probe_client: TestClient) -> None:
        """参数校验失败：code=10001，HTTP 200（10001 不在例外表中）。"""
        resp = probe_client.post("/probe/validate", json={"quantity": 0})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == int(CommonError.PARAM_INVALID)
        assert set(body) == ENVELOPE_KEYS

    def test_validation_error_detail_is_serializable(self, probe_client: TestClient) -> None:
        """校验详情必须能被 JSON 序列化，否则处理器自己会再抛一次变成 500。"""
        resp = probe_client.post("/probe/validate", json={"quantity": "not-a-number"})
        assert resp.status_code == 200
        detail = resp.json()["data"]
        assert isinstance(detail, list) and detail
        assert {"field", "reason"} == set(detail[0])

    def test_unhandled_exception_returns_500_with_10008(self, probe_client: TestClient) -> None:
        resp = probe_client.get("/probe/boom")
        assert resp.status_code == 500
        assert resp.json()["code"] == int(CommonError.SYSTEM_BUSY)

    def test_unhandled_exception_does_not_leak_details(self, probe_client: TestClient) -> None:
        """S6/PRD §10：栈信息与内部连接串绝不能回给客户端。"""
        raw = probe_client.get("/probe/boom").text
        for leak in ("RuntimeError", "Traceback", "mysql://", "secret", "aids_shop"):
            assert leak not in raw, f"500 响应泄漏了内部信息：{leak!r}"

    def test_unknown_route_returns_envelope_with_404(self, live_client: TestClient) -> None:
        """协议层错误保留 404，但响应体仍须是统一信封。"""
        resp = live_client.get("/definitely-not-a-route")
        assert resp.status_code == 404
        body = resp.json()
        assert set(body) == ENVELOPE_KEYS
        assert body["code"] == int(CommonError.NOT_FOUND)

    def test_every_envelope_has_exactly_three_keys(self, probe_client: TestClient) -> None:
        """遍历异常分支，响应体字段集合恒为 {code, message, data}。"""
        cases = [
            probe_client.get("/probe/business"),
            probe_client.get("/probe/unauthorized"),
            probe_client.get("/probe/data-forbidden"),
            probe_client.get("/probe/boom"),
            probe_client.post("/probe/validate", json={"quantity": 0}),
            probe_client.get("/definitely-not-a-route"),
        ]
        for resp in cases:
            assert set(resp.json()) == ENVELOPE_KEYS, f"{resp.request.url} 响应体结构漂移"


# =====================================================================
# 四、路由分组（BE-01 交付的 APIRouter 模块化分包）
# =====================================================================


class TestModuleWiring:
    # 模块清单随梯次增长：BE-03 增加 auth（Token 生命周期），其余待 BE-07 起填充
    EXPECTED_MODULES = {"auth", "user", "product", "order", "pay", "marketing", "admin"}

    def test_all_modules_are_registered(self) -> None:
        assert set(MODULE_ROUTERS) == self.EXPECTED_MODULES

    def test_each_module_has_its_own_router_instance(self) -> None:
        routers = list(MODULE_ROUTERS.values())
        assert len({id(r) for r in routers}) == len(routers), "模块之间复用了同一个 router"

    def test_modules_have_no_cross_prefix(self) -> None:
        """每个模块只能有一个前缀，且不能为空——空前缀会吞掉所有根路径。"""
        for name, router in MODULE_ROUTERS.items():
            assert router.prefix, f"{name} 未声明 prefix"
            assert router.prefix.startswith("/"), f"{name} 的 prefix 必须以 / 开头"

    def test_app_starts_with_only_health_wired(self, live_client: TestClient) -> None:
        """BE-01 是脚手架：六个模块只建分组、暂无接口，故只有 /health 可达。

        用「发请求」而非遍历 `app.routes` 断言：fastapi >= 0.14x 把 include_router
        的结果包成惰性 `_IncludedRouter`（**没有 `.path` 属性**），遍历 routes 会
        静默漏掉全部子路由，得到「/health 未注册」的假阴性 —— 结构断言在这里
        既不稳也没必要。
        """
        assert live_client.get("/health").status_code == 200
        for prefix in sorted(router.prefix for router in MODULE_ROUTERS.values()):
            assert live_client.get(prefix).status_code == 404, f"{prefix} 下已有接口，请更新本测试"
