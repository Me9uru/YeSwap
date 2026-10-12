"""Config, data init and export/import routes."""

from app.services import backup as backup_service
from app.workflows.maintenance import import_full_backup

from fastapi import APIRouter, Response
from pydantic import BaseModel
from typing import Optional
from app.configuration.repository import (
    get_buff_credentials,
    get_steam_credentials,
    load_app_config_validated,
    update_app_config_validated,
)

router = APIRouter()


class ConfigBody(BaseModel):
    config: dict


class ImportFullBody(BaseModel):
    app_config: Optional[dict] = None
    credentials: Optional[dict] = None
    transactions: Optional[dict] = None
    accounts: Optional[dict] = None
    log: Optional[list] = None


@router.get("/api/config")
def api_get_config(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"config": load_app_config_validated()}


@router.get("/api/config/credentials")
def api_get_credentials(response: Response):
    response.headers["Cache-Control"] = "no-store"
    steam = get_steam_credentials() or {}
    buff = get_buff_credentials() or {}
    return {
        "steam": {
            "cookies": steam.get("cookies") or "",
            "steam_id": steam.get("steam_id") or "",
        },
        "buff": {
            "cookies": buff.get("cookies") or "",
            "user_agent": buff.get("user_agent") or "",
        },
    }


@router.post("/api/config")
def api_save_config(body: ConfigBody):
    saved = update_app_config_validated(body.config)
    return {"ok": True, "config": saved}


from app.services.maintenance import _api_data_init_unlocked


@router.post("/api/data/init")
def api_data_init():
    from app.workflows.purchase import (
        PipelineMaintenanceBlocked,
        exclusive_pipeline_maintenance,
    )
    from app.runtime.pipeline_control import mark_shutdown_pending

    try:
        with exclusive_pipeline_maintenance("data_init"):
            mark_shutdown_pending()
            result = _api_data_init_unlocked()
            return result
    except PipelineMaintenanceBlocked as exc:
        return {
            "ok": False,
            "reconciliation_required": True,
            "error": str(exc),
        }


@router.get("/api/export_full/download")
def api_export_full_download():
    import json
    from fastapi.responses import Response
    from utils.time import (
        now_in_configured_timezone,
        resolve_configured_timezone,
    )

    app_config = load_app_config_validated()
    data = backup_service.api_export_full()
    configured_timezone, _timezone_label = resolve_configured_timezone(
        app_config.get("system") or {}
    )
    ts = now_in_configured_timezone(configured_timezone).strftime("%Y-%m-%dT%H-%M-%S")
    filename = f"full_backup_{ts}.json"
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/import_full")
def api_import_full(body: ImportFullBody):
    return import_full_backup(**body.model_dump())
