"""BUFF 服务端代发接口的载荷加密。

BUFF 的「手动+」发起报价接口要求客户端提交加密后的 Steam Cookie，由
BUFF 用该会话替用户发起 Steam 交易报价。加密方案与官方网页/APP 及成熟
开源实现（Steamauto 的 BuffApiCrypt）一致：
RSA(PKCS#1 v1.5) 加密随机 AES-128 密钥，AES-CBC 加密正文，
最终载荷为 base64(加密密钥 + IV + 密文)。
"""

import base64
import os

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad

# BUFF 官方 RSA-4096 公钥（base64 编码的 DER），仅用于加密提交给 BUFF 的载荷。
_BUFF_PUBLIC_KEY_B64 = """MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEArF75iD8PXTT+B5nAnnhw
qxg9I48t9uED7r6GuRcPYUZ0Ye3Vdvs71CVjuELyxALtj5cN+Pe1DwDSUAH1TF+9
dS7769gcJaMMdgEB6vyssm9fnPKB4KXqbUHdMT1MF2tylemDlqfsfpkV91wtAhHf
SkNtsQcPw4Juhn0IK+2xyvlm6HtXqFOkhial5T+miGBJk3snHfLPmQFsg/3EuHFM
tBzoLX29C46SNv/W33dwOk3mgIP1SMy4TLmm8CuyNiCuHPum53Q3RXSGrpR2nJps
4ICIWb0P3VZmPhCrDK1iWwwtVGj9jDkCT2zh+B18j26vfTkBDdac5s4sw739uAha
bH56BQflowPICHVWtptCEnORewxo/FDhFUtn4sjiQswgnTHJ6F/q0vwegRRsx0AT
f3SvpksR6dZuUqHzISthooQ/68PrJ8VaKfT17u43pif08/bFkZAkYdLev4Mk0SlZ
YOqpRoif+7Pi0yObTZ0bgpCwDb1kgAmqCHi9pFPS/LUMVqSqMa4maxAX2A8a/cbl
CJbjBHLn0zrZn3YW4hKlaVvGFG/Mmag+ALV5xII0y6JSoqdxlxpyhEmbOi/GCFMw
0Mn6lyvYDCvYVwS7UqLMw7NU3WXhbNUh8DgBSb5jo4yY9E42d24JiumZulzkSdgy
OSkVea8JGUUD8PliMtRJOQkCAwEAAQ=="""


def encrypt_buff_payload(content: str, public_key_b64: str = _BUFF_PUBLIC_KEY_B64) -> str:
    """加密要提交给 BUFF 服务端的正文（如 Steam Cookie 串）。

    返回 base64(RSA(随机AES密钥) + IV + AES-CBC(正文))。
    """

    aes_key = os.urandom(16)
    iv = os.urandom(16)
    public_key = RSA.import_key(base64.b64decode(public_key_b64))
    encrypted_key = PKCS1_v1_5.new(public_key).encrypt(aes_key)
    cipher = AES.new(aes_key, AES.MODE_CBC, iv)
    ciphertext = cipher.encrypt(pad(str(content).encode("utf-8"), AES.block_size))
    return base64.b64encode(encrypted_key + iv + ciphertext).decode("utf-8")
