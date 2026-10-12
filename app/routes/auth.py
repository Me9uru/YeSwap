"""HTTP authentication endpoints; login sessions live in the service layer."""

from fastapi import APIRouter
from pydantic import BaseModel
from app.services import auth_session, steam_guard

router = APIRouter()


class ReloginFinishBody(BaseModel):
    success: bool


class ManualCookieBody(BaseModel):
    cookies: str
    session_id: str = ""
    steam_id: str = ""
    user_agent: str = ""


@router.post("/api/auth/steam/relogin_start")
def api_auth_steam_relogin_start():
    return auth_session.api_auth_steam_relogin_start()


@router.post("/api/auth/steam/relogin_finish")
def api_auth_steam_relogin_finish(body: ReloginFinishBody):
    return auth_session.api_auth_steam_relogin_finish(body.success)


@router.post("/api/auth/buff/relogin_start")
def api_auth_buff_relogin_start():
    return auth_session.api_auth_buff_relogin_start()


@router.post("/api/auth/buff/relogin_finish")
def api_auth_buff_relogin_finish(body: ReloginFinishBody):
    return auth_session.api_auth_buff_relogin_finish(body.success)


@router.post("/api/auth/{relogin_type}/manual_cookie")
def api_auth_manual_cookie(relogin_type: str, body: ManualCookieBody):
    return auth_session.api_auth_manual_cookie(relogin_type, **body.model_dump())


@router.get("/api/steam_guard")
def api_steam_guard():
    return steam_guard.api_steam_guard()
