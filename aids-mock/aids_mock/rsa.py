"""RSA2 签名/验签（Mock 渠道侧）。

分工（与商户侧 `.env.example` 的 `MOCK_PAY_RSA_*` 对应）：
    - 商户（主业务服务）持商户私钥，对发往渠道的请求签名；Mock 渠道用
      **商户公钥**验签（`MOCK_MERCHANT_RSA_PUBLIC_KEY_PATH`）；
    - Mock 渠道持渠道私钥，对异步回调签名；商户用**渠道公钥**验签
      （即商户侧的 `MOCK_PAY_RSA_PUBLIC_KEY_PATH` —— 同一把公钥的两个名字）。

开发环境引导：密钥文件缺失时 `ensure_keypair()` 现场生成并落盘（Mock 只在
开发环境存在，PRD 边界声明）；生产语义下 Mock 整体不存在，故不做启动断言。

签名内容：`SHA-256(报文体) + "\n" + timestamp + "\n" + nonce` —— 报文体哈希
保证完整性，timestamp/nonce 由商户侧生成并回填进回调，形成可核对的对账链。
"""

from __future__ import annotations

import base64
import hashlib
import logging
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

logger = logging.getLogger(__name__)


def ensure_keypair(private_path: Path, public_path: Path) -> tuple[bytes, bytes]:
    """加载密钥对；缺失则现场生成并写入（开发引导）。返回 (私钥 PEM, 公钥 PEM)。"""
    if private_path.is_file() and public_path.is_file():
        return private_path.read_bytes(), public_path.read_bytes()

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(private_pem)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_bytes(public_pem)
    logger.warning(
        "RSA 密钥缺失，已现场生成（开发引导）：%s / %s —— 多实例部署需共享同一对",
        private_path,
        public_path,
    )
    return private_pem, public_pem


def _load_private(pem: bytes) -> rsa.RSAPrivateKey:
    """加载 RSA 私钥；非 RSA（Ed25519/EC/X25519…）直接拒绝。

    为什么要显式收窄而不是直接返回 `load_pem_private_key()` 的结果：
    后者的返回类型是**全部私钥的联合**，其中多数（X25519/Ed448…）没有 `sign()`
    签名形态也不兼容 RSA2 —— 不收窄时 pyright 会对每次调用报十几处
    「Cannot access attribute "sign"」，而这些错误是**真问题被类型系统提前暴露**：
    若真的用 EC 私钥去签，运行期才炸，且报错点离现场很远。
    """
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError(f"RSA2 只接受 RSA 私钥，得到 {type(key).__name__}")
    return key


def _load_public(pem: bytes) -> rsa.RSAPublicKey:
    """加载 RSA 公钥；非 RSA 直接拒绝（理由同 `_load_private`）。"""
    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, rsa.RSAPublicKey):
        raise TypeError(f"RSA2 只接受 RSA 公钥，得到 {type(key).__name__}")
    return key


def _sign_content(body_sha256: str, timestamp: str, nonce: str) -> bytes:
    return f"{body_sha256}\n{timestamp}\n{nonce}".encode()


def sign_rsa2(private_pem: bytes, body: bytes, timestamp: str, nonce: str) -> str:
    """RSA2（RSA-SHA256）签名：对 `SHA256(body) + timestamp + nonce` 签名，base64 输出。"""
    body_hash = hashlib.sha256(body).hexdigest()
    signature = _load_private(private_pem).sign(
        _sign_content(body_hash, timestamp, nonce),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def verify_rsa2(
    public_pem: bytes, body: bytes, timestamp: str, nonce: str, signature_b64: str
) -> bool:
    """验签。任何异常（格式/密钥不匹配）一律 False。"""
    try:
        body_hash = hashlib.sha256(body).hexdigest()
        _load_public(public_pem).verify(
            base64.b64decode(signature_b64),
            _sign_content(body_hash, timestamp, nonce),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:  # noqa: BLE001 - 验签失败的原因不影响"拒绝"这个结果
        return False


def body_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()
