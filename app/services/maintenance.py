"""Application data reset, guarded by the route maintenance lock."""

from app.runtime.state import (
    clear_transactions,
)
from app.configuration.repository import (
    save_app_config_validated,
)
from config import save_credentials
from app.accounts.repository import replace_all as accounts_replace_all


def _api_data_init_unlocked():
    from app.runtime.state import clear_log
    from pathlib import Path

    # 清理内存状态
    clear_transactions()
    clear_log()
    # 先使账号内存缓存失效，防止旧账号（含非人民币币种）残留在内存中
    import app.accounts.repository as _accounts_mod

    _accounts_mod._cache = None
    accounts_replace_all({"accounts": [], "current_id": None})
    # The route-level maintenance coordinator already owns the BUFF auth lock.
    save_credentials({})

    # 构建一份干净的默认配置（保留功能性默认值，清除所有个人凭据）
    clean_config = {
        "buff": {
            "pay_method": "wechat",
            "game": "csgo",
        },
        "pipeline": {
            "max_discount": 0.8,
            "exclude_keywords": ["印花"],
            "verbose_debug": False,
            "steam_listings_debug": False,
        },
        "proxy_pool": {
            "enabled": False,
            "strategy": 1,
            "proxies": [],
            "webshare_api_key": "",
        },
        # 以下敏感字段重置为空
        "steam_guard": {
            "shared_secret": "",
        },
        "steam_confirm": {
            "enabled": False,
            "identity_secret": "",
            "device_id": "",
        },
        "notify": {
            "email_user": "",
            "email_pass": "",
            "imap_server": "",
            "target_sender": "",
            "allowed_sender": "",
            "subject_success": "",
            "subject_fail": "",
        },
    }
    save_app_config_validated(clean_config)

    # 删除历史文件及缓存
    from utils.paths import DATA_DIR

    config_dir = DATA_DIR
    files_to_remove = [
        "exchange_rate.json",
        "steam_userdata.json",
        "transactions.json.bak",
        "buff_request_policy.json",
        "buff_request_policy.json.tmp",
        "buff_checkout_guard.json",
    ]
    for file_name in files_to_remove:
        file_path = config_dir / file_name
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                pass
    for pattern in (
        ".buff_request_policy.json.*.tmp",
        ".buff_checkout_guard.json.*.tmp",
    ):
        for temp_file in config_dir.glob(pattern):
            try:
                temp_file.unlink()
            except Exception:
                pass

    # 删除 Playwright 浏览器用户数据目录（包含 Cookie 等敏感信息）
    import shutil

    for dir_name in ["playwright_steam", "playwright_buff"]:
        dir_path = config_dir / dir_name
        if dir_path.exists() and dir_path.is_dir():
            try:
                shutil.rmtree(dir_path)
            except Exception:
                pass

    # 删除磁盘日志文件
    log_dir = Path("log")
    if log_dir.exists():
        for log_file in log_dir.glob("*.log"):
            try:
                log_file.unlink()
            except Exception:
                pass

    # 清空数据库 (丢弃所有表并重建)
    try:
        from app.storage.connection import get_engine, init_db
        from sqlmodel import SQLModel

        engine = get_engine()
        SQLModel.metadata.drop_all(engine)
        init_db()
    except Exception:
        pass

    import os
    import threading

    def _shutdown():
        import time

        time.sleep(1.5)
        os._exit(0)

    threading.Thread(target=_shutdown, daemon=True).start()

    return {"ok": True}
