"""
FastAPI application entry point.
All routes are registered via `app.routes`, and background jobs are imported
from their owning modules. This file is intentionally kept minimal.
"""

import asyncio
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.storage.connection import init_db
from app.storage.transactions import migrate_from_json
from app.background.accounts import sync_account_region_worker
from app.background.exchange_rates import exchange_rate_worker
from app.background.keepalive import session_keepalive_worker
from app.background.listings import listing_check_worker
from app.background.receive import receive_worker
from app.services.task_queue import get_task_queue, shutdown_task_queue
from app.services.lifecycle import stop_event
from app.services.auth_session import configure_pipeline_resume
from app.runtime.state import log, request_stop
from app.workflows.purchase import start_pipeline

_bg_started = False
_bg_lock = threading.Lock()


def _start_background_workers() -> None:
    global _bg_started
    with _bg_lock:
        if _bg_started:
            return
        stop_event.clear()
        _bg_started = True
    q = get_task_queue()
    q.submit(
        sync_account_region_worker, name="sync_account_region", max_retries=3, retry_base_delay=10.0
    )
    for fn in (
        receive_worker,
        listing_check_worker,
        exchange_rate_worker,
        session_keepalive_worker,
    ):
        q.submit(fn, name=fn.__name__, max_retries=3, retry_base_delay=5.0)


@asynccontextmanager
async def _lifespan(application: FastAPI):
    global _bg_started
    from steam.market_orders import configure_nameid_cache
    from app.storage.market_cache import db_get_item_nameid, db_set_item_nameid
    from utils.proxy_manager import configure_proxy_runtime
    from app.configuration.repository import load_app_config_validated

    configure_proxy_runtime(load_app_config_validated, log)
    configure_nameid_cache(db_get_item_nameid, db_set_item_nameid)
    init_db()
    migrate_from_json()
    _start_background_workers()
    try:
        yield
    finally:
        request_stop()
        stop_event.set()
        await asyncio.to_thread(shutdown_task_queue)
        with _bg_lock:
            _bg_started = False


app = FastAPI(title="aetherswap", lifespan=_lifespan)
from app.routes import register_routes

register_routes(app)
configure_pipeline_resume(start_pipeline)
