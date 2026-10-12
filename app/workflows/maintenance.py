"""排斥交易写入的配置和数据维护编排。"""

from app.services import backup as backup_service
from app.workflows.purchase import PipelineMaintenanceBlocked, exclusive_pipeline_maintenance


def import_full_backup(**sections):
    try:
        with exclusive_pipeline_maintenance("full_import"):
            return backup_service.restore_full_backup_unlocked(**sections)
    except PipelineMaintenanceBlocked as exc:
        return {
            "ok": False,
            "reconciliation_required": True,
            "error": str(exc),
        }
