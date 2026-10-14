import base64

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import unpad

from buff.crypto import encrypt_buff_payload


def test_encrypt_buff_payload_round_trips_with_test_key():
    key = RSA.generate(2048)
    public_b64 = base64.b64encode(key.publickey().export_key("DER")).decode("utf-8")
    content = "steamLoginSecure=abc123; sessionid=xyz"

    raw = base64.b64decode(
        encrypt_buff_payload(content, public_key_b64=public_b64)
    )

    rsa_len = 256  # 2048 位密钥的一个加密块
    aes_key = PKCS1_v1_5.new(key).decrypt(raw[:rsa_len], None)
    assert aes_key is not None and len(aes_key) == 16
    iv = raw[rsa_len : rsa_len + 16]
    body = raw[rsa_len + 16 :]
    plain = unpad(AES.new(aes_key, AES.MODE_CBC, iv).decrypt(body), AES.block_size)
    assert plain.decode("utf-8") == content


def test_encrypt_buff_payload_uses_official_key_structure():
    raw = base64.b64decode(encrypt_buff_payload("a=1"))

    # RSA-4096 加密的 AES 密钥（512 字节）+ IV（16）+ 一个 AES 密文块（16）
    assert len(raw) == 512 + 16 + 16
