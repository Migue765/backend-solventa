from typing import cast

from flask import current_app, request

from bff_web.application.auth_service import AuthService
from bff_web.config import Config
from bff_web.domain.auth import User
from bff_web.domain.errors import UnauthorizedError


def authenticated_user() -> User | None:
    config = cast(Config, current_app.extensions["bff_config"])
    auth_service = cast(AuthService, current_app.extensions["auth_service"])
    try:
        session = auth_service.current_session(request.cookies.get(config.session_cookie_name))
    except UnauthorizedError:
        return None
    return session.user
