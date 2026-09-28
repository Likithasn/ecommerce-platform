import datetime as dt
import os
from contextlib import asynccontextmanager

import bcrypt
import jwt
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from auth import ALG, SECRET, current_user
from database import Base, engine, get_db

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com").lower()
EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(20), default="customer")


class RegisterIn(BaseModel):
    email: str = Field(pattern=EMAIL_RE)
    password: str = Field(min_length=6)
    full_name: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


class ProfileUpdate(BaseModel):
    full_name: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="User Service", lifespan=lifespan)


def user_out(u: User):
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role}


def make_token(u: User) -> str:
    payload = {
        "sub": str(u.id),
        "email": u.email,
        "role": u.role,
        "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=12),
    }
    return jwt.encode(payload, SECRET, algorithm=ALG)


@app.get("/health")
def health():
    return {"status": "ok", "service": "user"}


@app.post("/users/register", status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Email already registered")
    hashed = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    role = "admin" if email == ADMIN_EMAIL else "customer"
    user = User(email=email, password_hash=hashed, full_name=body.full_name, role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user_out(user)


@app.post("/users/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not bcrypt.checkpw(body.password.encode(), user.password_hash.encode()):
        raise HTTPException(401, "Invalid email or password")
    return {"access_token": make_token(user), "token_type": "bearer"}


@app.get("/users/me")
def me(claims=Depends(current_user), db: Session = Depends(get_db)):
    user = db.get(User, claims["id"])
    if not user:
        raise HTTPException(404, "User not found")
    return user_out(user)


@app.put("/users/me")
def update_me(body: ProfileUpdate, claims=Depends(current_user), db: Session = Depends(get_db)):
    user = db.get(User, claims["id"])
    if not user:
        raise HTTPException(404, "User not found")
    user.full_name = body.full_name
    db.commit()
    return user_out(user)
