from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import User, SessionToken
from ..security import (
    hash_password, verify_password, create_session, destroy_session,
    current_user, current_session, SESSION_COOKIE, SESSION_DAYS,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: str
    name: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


def _set_cookie(response: Response, token: str):
    response.set_cookie(SESSION_COOKIE, token, httponly=True,
                        samesite="strict", max_age=SESSION_DAYS * 24 * 3600)


@router.post("/register")
def register(body: RegisterIn, response: Response,
             db: Session = Depends(get_session)):
    if db.exec(select(User).where(User.email == body.email)).first():
        raise HTTPException(400, "Email already registered")
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    user = User(email=body.email, name=body.name,
                password_hash=hash_password(body.password), role="attendee")
    db.add(user); db.commit(); db.refresh(user)
    tok = create_session(db, user.id)
    _set_cookie(response, tok.id)
    return {"ok": True, "user_id": user.id, "role": user.role, "csrf": tok.csrf}


@router.post("/login")
def login(body: LoginIn, response: Response,
          db: Session = Depends(get_session)):
    user = db.exec(select(User).where(User.email == body.email)).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    tok = create_session(db, user.id)
    _set_cookie(response, tok.id)
    return {"ok": True, "user_id": user.id, "role": user.role, "csrf": tok.csrf}


@router.post("/logout")
def logout(response: Response, db: Session = Depends(get_session),
           sess: SessionToken | None = Depends(current_session)):
    if sess:
        destroy_session(db, sess.id)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
def me(user: User | None = Depends(current_user),
       sess: SessionToken | None = Depends(current_session)):
    if not user:
        return {"authenticated": False}
    return {"authenticated": True, "user_id": user.id, "email": user.email,
            "name": user.name, "role": user.role,
            "csrf": sess.csrf if sess else None}