from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_admin
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models import User
from app.models.user import ROLES
from app.schemas import Token, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/auth/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form.username).first()
    if not user or not user.is_active or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Kullanici adi veya sifre hatali")
    return Token(access_token=create_access_token(user.username, user.role))


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.query(User).order_by(User.username).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(data: UserCreate, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if data.role not in ROLES:
        raise HTTPException(400, f"Rol su degerlerden biri olmali: {', '.join(ROLES)}")
    if data.role == "owner" and admin.role != "owner":
        raise HTTPException(403, "Owner rolunu yalnizca owner atayabilir")
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(400, "Bu kullanici adi zaten var")
    u = User(username=data.username, full_name=data.full_name, role=data.role, hashed_password=hash_password(data.password))
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, data: UserUpdate, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "Kullanici bulunamadi")
    if data.role is not None:
        if data.role not in ROLES:
            raise HTTPException(400, "Gecersiz rol")
        if data.role == "owner" and admin.role != "owner":
            raise HTTPException(403, "Owner rolunu yalnizca owner atayabilir")
        if u.role == "owner" and data.role != "owner" and admin.role != "owner":
            raise HTTPException(403, "Owner rolunu yalnizca owner degistirebilir")
        u.role = data.role
    if data.full_name is not None:
        u.full_name = data.full_name
    if data.password:
        u.hashed_password = hash_password(data.password)
    if data.is_active is not None:
        if u.id == admin.id and not data.is_active:
            raise HTTPException(400, "Kendi hesabinizi pasife alamazsiniz")
        u.is_active = data.is_active
    db.commit()
    db.refresh(u)
    return u
