"""邮件付款确认服务。"""
import imaplib
import email
import time
from email.header import decode_header
from typing import Callable, Optional
def wait_email_command(
    cfg: dict,
    timeout_seconds: int = 300,
    is_stop_requested: Optional[Callable[[], bool]] = None,
    log_fn: Optional[Callable[[str, str], None]] = None,
) -> str:
    n = cfg.get("notify") or {}
    user = (n.get("email_user") or "").strip()
    passwd = (n.get("email_pass") or "").strip()
    server = (n.get("imap_server") or "imap.qq.com").strip()
    target_sender = (n.get("target_sender") or "").strip()
    subject_success = (n.get("subject_success") or "已确认成功付款").strip()
    subject_fail = (n.get("subject_fail") or "已确认付款失败").strip()
    allowed_sender = (n.get("allowed_sender") or "").strip()
    if not user or not passwd:
        return "timeout"
    start = time.time()
    timeout = min(timeout_seconds, 86400)
    if log_fn:
        log_fn(f"监听邮箱指令 发件人含「{target_sender}」 成功标题「{subject_success}」 失败标题「{subject_fail}」", "info")
    while time.time() - start < timeout:
        if is_stop_requested and is_stop_requested():
            return "timeout"
        mail = None
        try:
            mail = imaplib.IMAP4_SSL(server)
            mail.login(user, passwd)
            mail.select("inbox")
            status, messages = mail.search(None, "UNSEEN")
            email_ids = (messages or [b""])[0].split()
            if not email_ids:
                time.sleep(3)
                continue
            latest_id = email_ids[-1]
            _, msg_data = mail.fetch(latest_id, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            sender_header = msg.get("From", "")
            sender_name, sender_addr = email.utils.parseaddr(sender_header)
            full_sender = f"{sender_name} {sender_addr}"
            subject_parts = decode_header(msg.get("Subject", ""))
            parts = []
            for part, encoding in subject_parts:
                if isinstance(part, bytes):
                    enc = (encoding or "utf-8").strip().lower()
                    if enc in ("unknown-8bit", "unknown", ""):
                        enc = "utf-8"
                    try:
                        parts.append(part.decode(enc, errors="replace"))
                    except LookupError:
                        parts.append(part.decode("utf-8", errors="replace"))
                else:
                    parts.append(str(part))
            subject_str = "".join(parts)
            if log_fn:
                log_fn(f"收到邮件 发件人={full_sender[:50]} 标题={subject_str[:40]}", "info")
            is_sender_ok = target_sender.lower() in full_sender.lower() if target_sender else True
            if allowed_sender and sender_addr:
                is_sender_ok = is_sender_ok and (allowed_sender.lower() in (sender_addr or "").lower())
            if not is_sender_ok:
                mail.store(latest_id, "+FLAGS", "\\Seen")
                mail.expunge()
                time.sleep(3)
                continue
            if subject_success and subject_success in subject_str:
                mail.store(latest_id, "+FLAGS", "\\Deleted")
                mail.expunge()
                if log_fn:
                    log_fn("邮件指令: 已确认成功付款", "info")
                return "success"
            if subject_fail and subject_fail in subject_str:
                mail.store(latest_id, "+FLAGS", "\\Deleted")
                mail.expunge()
                if log_fn:
                    log_fn("邮件指令: 已确认付款失败", "info")
                return "fail"
            mail.store(latest_id, "+FLAGS", "\\Seen")
            mail.expunge()
        except Exception as e:
            if log_fn:
                log_fn(f"邮箱连接/解析异常: {e}", "warn")
        finally:
            if mail is not None:
                try:
                    mail.logout()
                except Exception:
                    pass
            mail = None
        time.sleep(3)
    if log_fn:
        log_fn("邮箱等待超时", "warn")
    return "timeout"
