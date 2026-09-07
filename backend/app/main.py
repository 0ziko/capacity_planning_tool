import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, imports, master, planning
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.migrate import ensure_columns, repair_orphans
from app.db.session import Base, SessionLocal, engine
from app.models import User


def seed_admin() -> None:
    s = get_settings()
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.role == "admin").first():
            db.add(User(username=s.first_admin_username, full_name="Yonetici", role="admin", hashed_password=hash_password(s.first_admin_password)))
            db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_columns(engine)
    removed = repair_orphans(engine)
    if removed:
        logging.getLogger("uvicorn.error").warning("Yetim kayitlar temizlendi: %s", removed)
    seed_admin()
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(auth.router)
app.include_router(master.router)
app.include_router(planning.router)
app.include_router(imports.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name}
