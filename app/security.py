import secrets
from datetime import datetime
from typing import Optional

from fastapi import Depends, HTTPException, Request
from passlib.context import CryptContext
from sqlmodel import Session

from .db import get_session
from .models import SessionToken, User

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
SESSION_COOKIE = "eventsphere_session"
SESSION_DAYS = 7


def hash_password(pw: str) -> str:
    return pwd_ctx.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    return pwd_ctx.verify(pw, hashed)


def create_session(db: Session, user_id: int) -> SessionToken:
    tok = SessionToken(
        id=secrets.token_urlsafe(32),
        user_id=user_id,
        csrf=secrets.token_urlsafe(32),
    )
    db.add(tok); db.commit(); db.refresh(tok)
    return tok


def destroy_session(db: Session, token_id: str) -> None:
    tok = db.get(SessionToken, token_id)
    if tok:
        db.delete(tok); db.commit()


# ---------- FastAPI dependencies ----------

def current_session(request: Request,
                    db: Session = Depends(get_session)) -> Optional[SessionToken]:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    return db.get(SessionToken, cookie)


def current_user(request: Request,
                 db: Session = Depends(get_session)) -> Optional[User]:
    sess = current_session(request, db)
    if not sess:
        return None
    return db.get(User, sess.user_id)


def require_user(user: Optional[User] = Depends(current_user)) -> User:
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def require_role(*roles: str):
    """Usage: admin: User = Depends(require_role('admin'))"""
    def dep(user: User = Depends(require_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail=f"Requires role {roles}")
        return user
    return dep