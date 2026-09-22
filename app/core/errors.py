"""错误码唯一可信源（SSOT）。

=====================================================================
本文件是全项目错误码的**唯一权威定义**。
=====================================================================

权威文档：docs/API.md §1.2 错误码分段 / §1.3 通用错误码明细 + 各业务模块码表
由 tests/contract/test_error_codes.py 自动校验。

分段规则（API.md §1.2）：
    | 段位   | 域        | 说明                        |
    |--------|-----------|-----------------------------|
    | 0      | 成功      | —                           |
    | 1xxxx  | 通用      | 参数、鉴权、限流、系统异常    |
    | 2xxxx  | 用户      | 注册登录、地址               |
    | 3xxxx  | 商品      | 商品、类目、SKU、库存         |
    | 4xxxx  | 订单      | 订单、售后                   |
    | 5xxxx  | 支付      | 支付、退款、对账             |
    | 6xxxx  | AI 客服   | 会话、知识库                 |
    | 7xxxx  | 营销      | 优惠券、运费                 |
    | 8xxxx  | 后台权限  | RBAC、审计                   |
    | 9xxxx  | 文件/系统 | 上传、配置                   |

使用规范（API.md §1.3）：
    后端抛业务异常时必须指定错误码，**禁止全部返回 SYSTEM_BUSY(10008)**。
    新增错误码须同步 docs/API.md。
"""

from __future__ import annotations

from enum import IntEnum
from typing import Final

# =====================================================================
# 0 — 成功
# =====================================================================

SUCCESS: Final[int] = 0


# =====================================================================
# 1xxxx — 通用
# =====================================================================


class CommonError(IntEnum):
    """API.md §1.3 通用错误码明细"""

    PARAM_INVALID = 10001  # 参数校验失败
    UNAUTHORIZED = 10002  # 未登录或 Token 失效
    FORBIDDEN = 10003  # 无权限访问
    NOT_FOUND = 10004  # 资源不存在
    DATA_FORBIDDEN = 10005  # 数据越权（IDOR 拦截），不暴露资源是否存在
    RATE_LIMITED = 10006  # 请求过于频繁
    DUPLICATE_SUBMIT = 10007  # 重复提交
    SYSTEM_BUSY = 10008  # 系统繁忙
    FILE_INVALID = 10009  # 文件类型/大小不合法


# =====================================================================
# 2xxxx — 用户
# =====================================================================


class UserError(IntEnum):
    """API.md §2.1 用户模块"""

    MOBILE_INVALID = 20001  # 手机号格式错误
    SMS_TOO_FREQUENT = 20002  # 验证码发送过于频繁（60s 内重复发送）
    SMS_DAILY_LIMIT = 20003  # 当日发送次数超限
    LOGIN_FAILED = 20004  # 账号或密码错误
    ACCOUNT_LOCKED = 20005  # 账号被锁定（连续失败 5 次，锁 30min）
    ACCOUNT_DISABLED = 20006  # 账号被禁用
    ADDRESS_LIMIT = 20007  # 收货地址数量超限（API.md §2.4）


# =====================================================================
# 3xxxx — 商品
# =====================================================================


class ProductError(IntEnum):
    """API.md §2.2 商品模块 / §2.7 评价 / §3.2 商品与库存"""

    SPU_NOT_FOUND = 30001  # 商品不存在或已下架
    SPU_OFF_SHELF = 30002  # 商品已下架
    STOCK_INSUFFICIENT = 30003  # 库存不足（data 中返回具体 skuId 与剩余量）
    REVIEW_DUPLICATE = 30004  # 该订单项已评价（API.md §2.7）
    STOCK_ADJUST_INVALID = 30005  # 库存调整参数不合法（API.md §3.2）


# =====================================================================
# 4xxxx — 订单
# =====================================================================


class OrderError(IntEnum):
    """API.md §2.4 订单 / §2.5 支付 / §2.6 售后 / §3.3 订单与售后"""

    AMOUNT_MISMATCH = 40001  # 订单金额校验失败（前端金额与服务端不一致）
    ORDER_NOT_FOUND = 40002  # 订单不存在
    STATUS_NOT_PAYABLE = 40003  # 订单状态不允许支付（非待付款）
    ORDER_TIMEOUT_CLOSED = 40004  # 订单已超时关闭
    STATUS_NOT_AFTER_SALE = 40005  # 订单状态不允许申请售后
    AFTER_SALE_IN_PROGRESS = 40006  # 该订单项已有进行中的售后单
    AFTER_SALE_EXPIRED = 40007  # 超出可申请时限（签收超 7 天）
    AFTER_SALE_STATUS_INVALID = 40008  # 售后状态不允许此操作
    STATUS_NOT_SHIPPABLE = 40009  # 订单状态不允许发货（非待发货）（API.md §3.3）
    DELIVERY_NO_EXISTS = 40010  # 运单号已存在（API.md §3.3）


# =====================================================================
# 5xxxx — 支付
# =====================================================================


class PaymentError(IntEnum):
    """API.md §2.5 支付模块"""

    PAYMENT_CREATE_FAILED = 50001  # 支付单创建失败
    SIGN_VERIFY_FAILED = 50002  # 验签失败（记录审计日志）
    CALLBACK_AMOUNT_MISMATCH = 50003  # 回调金额与订单金额不符（告警）
    PAYMENT_NOT_FOUND = 50004  # 支付单不存在


# =====================================================================
# 6xxxx — AI 客服
# =====================================================================


class AiError(IntEnum):
    """API.md §五 AI 客服 SSE 接口"""

    ARK_DEGRADED = 60001  # Ark 服务不可用，已降级（返回 FAQ 原文 + 转人工提示）
    SENSITIVE_BLOCKED = 60002  # 输入命中敏感词，已拦截
    CONVERSATION_NOT_FOUND = 60003  # 会话不存在或不属于当前用户
    CONCURRENCY_LIMIT = 60004  # 并发对话数超限


# =====================================================================
# 7xxxx — 营销
# =====================================================================


class MarketingError(IntEnum):
    """API.md §2.4 / §2.7 优惠券与运费"""

    COUPON_UNAVAILABLE = 70001  # 优惠券不可用（已过期/已使用/不满足门槛）
    COUPON_NOT_OWNED = 70002  # 优惠券不属于当前用户
    COUPON_EXHAUSTED = 70003  # 优惠券已领完
    COUPON_PER_USER_LIMIT = 70004  # 超出每人限领数量
    COUPON_NOT_IN_PERIOD = 70005  # 优惠券未开始或已结束


# =====================================================================
# 8xxxx — 后台权限
# =====================================================================


class AdminError(IntEnum):
    """API.md §3.2 商品与库存"""

    ADJUST_REASON_REQUIRED = 80001  # 缺少调整原因


# =====================================================================
# 9xxxx — 文件/系统
# =====================================================================


class FileSystemError(IntEnum):
    """API.md §1.2 分段表 `9xxxx` 文件/系统（上传、配置）。

    **当前无成员**：T0 阶段尚无文件/系统域的业务错误码，
    上传相关的 `CommonError.FILE_INVALID(10009)` 是既有契约（API.md §1.3 已登记），
    迁移会破坏前端已对接的码，故保持不动。

    本类存在的意义是"**认领段位**"：
        API.md §1.2 与 PRD §5.4 都声明了 9 段位，若代码侧没有归属枚举类，
        该段位就成了①文档说存在、②代码不认、③扫描器也扫不到的**悬空段**
        （`scan_error_codes.py` 的区间曾止于 89999，9xxxx 裸数字永远漏检）。
    由 `test_error_codes.py::TestSegmentOwnership` 断言"每个非 0 段位都必须有归属类"，
    因此本类不可删除；T1 新增上传/配置错误码时直接在此登记，并同步 docs/API.md。
    """


# =====================================================================
# 错误码 → 分段域映射（契约测试的锚点）
# =====================================================================

# 段位首数字 → 域名称，与 API.md §1.2 分段表逐字对应
ERROR_SEGMENTS: Final[dict[int, str]] = {
    0: "成功",
    1: "通用",
    2: "用户",
    3: "商品",
    4: "订单",
    5: "支付",
    6: "AI 客服",
    7: "营销",
    8: "后台权限",
    9: "文件/系统",
}

# 每个错误码枚举类必须归属的段位首数字。
# test_error_codes.py 用它校验：a) 段位正确 b) 与 API.md 表格一致
ERROR_DOMAIN_MAP: Final[dict[int, type[IntEnum]]] = {
    1: CommonError,
    2: UserError,
    3: ProductError,
    4: OrderError,
    5: PaymentError,
    6: AiError,
    7: MarketingError,
    8: AdminError,
    9: FileSystemError,
}


def all_error_classes() -> list[type[IntEnum]]:
    """返回全部业务错误码枚举类（供契约测试遍历）。"""
    return list(ERROR_DOMAIN_MAP.values())


def segment_of(code: int) -> int:
    """返回错误码所属段位首数字（0 表示成功）。"""
    if code == 0:
        return 0
    return int(str(code)[0])
