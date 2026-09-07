from collections.abc import Callable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db.session import get_db
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

ROLE_RANK = {"user": 1, "poweruser": 2, "admin": 3}


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    cred_exc = HTTPException(status.HTTP_401_UNAUTHORIZED, "Gecersiz veya suresi dolmus oturum")
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise cred_exc
    user = db.query(User).filter(User.username == payload.get("sub")).first()
    if not user or not user.is_active:
        raise cred_exc
    return user


def require_role(min_role: str) -> Callable:
    def checker(user: User = Depends(get_current_user)) -> User:
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK[min_role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu islem icin yetkiniz yok")
        return user

    return checker


require_user = require_role("user")
require_poweruser = require_role("poweruser")
require_admin = require_role("admin")
