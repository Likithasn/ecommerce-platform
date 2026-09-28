import os
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Float, String, UniqueConstraint, delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from auth import current_user
from database import Base, engine, get_db

PRODUCT_URL = os.getenv("PRODUCT_SERVICE_URL", "http://product-service:8000")


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("user_id", "product_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    product_id: Mapped[int]
    name: Mapped[str] = mapped_column(String(255))
    price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int]


class AddItem(BaseModel):
    product_id: int
    quantity: int = Field(default=1, gt=0)


class SetQuantity(BaseModel):
    quantity: int = Field(gt=0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Shopping Cart Service", lifespan=lifespan)


def fetch_product(product_id: int) -> dict:
    try:
        r = httpx.get(f"{PRODUCT_URL}/products/{product_id}", timeout=5)
    except httpx.HTTPError:
        raise HTTPException(502, "Product service unavailable")
    if r.status_code == 404:
        raise HTTPException(404, "Product not found")
    r.raise_for_status()
    return r.json()


def cart_out(db: Session, user_id: int):
    items = db.scalars(select(CartItem).where(CartItem.user_id == user_id).order_by(CartItem.id)).all()
    rows = [
        {"product_id": i.product_id, "name": i.name, "price": i.price,
         "quantity": i.quantity, "subtotal": round(i.price * i.quantity, 2)}
        for i in items
    ]
    return {"user_id": user_id, "items": rows, "total": round(sum(r["subtotal"] for r in rows), 2)}


@app.get("/health")
def health():
    return {"status": "ok", "service": "cart"}


@app.get("/cart")
def get_cart(user=Depends(current_user), db: Session = Depends(get_db)):
    return cart_out(db, user["id"])


@app.post("/cart/items", status_code=201)
def add_item(body: AddItem, user=Depends(current_user), db: Session = Depends(get_db)):
    product = fetch_product(body.product_id)
    item = db.scalar(select(CartItem).where(
        CartItem.user_id == user["id"], CartItem.product_id == body.product_id))
    new_qty = body.quantity + (item.quantity if item else 0)
    if new_qty > product["stock"]:
        raise HTTPException(409, f"Only {product['stock']} in stock")
    if item:
        item.quantity = new_qty
        item.price = product["price"]
    else:
        db.add(CartItem(user_id=user["id"], product_id=body.product_id, name=product["name"],
                        price=product["price"], quantity=new_qty))
    db.commit()
    return cart_out(db, user["id"])


@app.put("/cart/items/{product_id}")
def set_quantity(product_id: int, body: SetQuantity, user=Depends(current_user),
                 db: Session = Depends(get_db)):
    item = db.scalar(select(CartItem).where(
        CartItem.user_id == user["id"], CartItem.product_id == product_id))
    if not item:
        raise HTTPException(404, "Item not in cart")
    product = fetch_product(product_id)
    if body.quantity > product["stock"]:
        raise HTTPException(409, f"Only {product['stock']} in stock")
    item.quantity = body.quantity
    db.commit()
    return cart_out(db, user["id"])


@app.delete("/cart/items/{product_id}")
def remove_item(product_id: int, user=Depends(current_user), db: Session = Depends(get_db)):
    db.execute(delete(CartItem).where(
        CartItem.user_id == user["id"], CartItem.product_id == product_id))
    db.commit()
    return cart_out(db, user["id"])


@app.delete("/cart")
def clear_cart(user=Depends(current_user), db: Session = Depends(get_db)):
    db.execute(delete(CartItem).where(CartItem.user_id == user["id"]))
    db.commit()
    return cart_out(db, user["id"])
