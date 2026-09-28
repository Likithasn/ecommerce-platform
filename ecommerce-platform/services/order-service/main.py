import datetime as dt
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import DateTime, Float, ForeignKey, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from auth import current_user, require_admin
from database import Base, engine, get_db

CART_URL = os.getenv("CART_SERVICE_URL", "http://cart-service:8000")
PRODUCT_URL = os.getenv("PRODUCT_SERVICE_URL", "http://product-service:8000")
PAYMENT_URL = os.getenv("PAYMENT_SERVICE_URL", "http://payment-service:8000")
NOTIFY_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://notification-service:8000")

ADMIN_STATUSES = {"SHIPPED", "DELIVERED", "CANCELLED"}


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    user_email: Mapped[str] = mapped_column(String(255))
    total: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    items: Mapped[list["OrderItem"]] = relationship(cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[int]
    name: Mapped[str] = mapped_column(String(255))
    price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int]


class OrderIn(BaseModel):
    card_number: str = "4242424242424242"


class StatusIn(BaseModel):
    status: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Order Service", lifespan=lifespan)


def order_out(o: Order):
    return {
        "id": o.id, "user_id": o.user_id, "status": o.status, "total": o.total,
        "created_at": o.created_at.isoformat(),
        "items": [{"product_id": i.product_id, "name": i.name, "price": i.price,
                   "quantity": i.quantity} for i in o.items],
    }


def notify(email: str, event: str, message: str):
    try:
        httpx.post(f"{NOTIFY_URL}/notify", timeout=3,
                   json={"email": email, "event": event, "message": message})
    except httpx.HTTPError:
        pass  # notifications must never break an order


@app.get("/health")
def health():
    return {"status": "ok", "service": "order"}


@app.post("/orders", status_code=201)
def place_order(body: OrderIn, request: Request, user=Depends(current_user),
                db: Session = Depends(get_db)):
    auth = {"Authorization": request.headers["Authorization"]}
    try:
        with httpx.Client(timeout=10) as client:
            cart_resp = client.get(f"{CART_URL}/cart", headers=auth)
            cart_resp.raise_for_status()
            cart = cart_resp.json()
            if not cart["items"]:
                raise HTTPException(400, "Cart is empty")

            lines = [{"product_id": i["product_id"], "quantity": i["quantity"]}
                     for i in cart["items"]]
            res = client.post(f"{PRODUCT_URL}/products/internal/reserve", json={"items": lines})
            if res.status_code != 200:
                code = res.status_code if res.status_code in (404, 409) else 502
                raise HTTPException(code, res.json().get("detail", "Could not reserve stock"))

            order = Order(user_id=user["id"], user_email=user["email"],
                          total=cart["total"], status="PENDING")
            order.items = [OrderItem(product_id=i["product_id"], name=i["name"],
                                     price=i["price"], quantity=i["quantity"])
                           for i in cart["items"]]
            db.add(order)
            db.commit()
            db.refresh(order)

            try:
                pay = client.post(f"{PAYMENT_URL}/pay", json={
                    "order_id": order.id, "amount": order.total,
                    "card_number": body.card_number})
                paid = pay.status_code == 200 and pay.json().get("status") == "SUCCEEDED"
            except httpx.HTTPError:
                paid = False

            if paid:
                order.status = "PAID"
                client.delete(f"{CART_URL}/cart", headers=auth)
            else:
                order.status = "PAYMENT_FAILED"
                try:  # put the stock back
                    client.post(f"{PRODUCT_URL}/products/internal/release", json={"items": lines})
                except httpx.HTTPError:
                    pass
            db.commit()
    except httpx.HTTPError:
        raise HTTPException(502, "A downstream service is unavailable")

    if order.status == "PAID":
        notify(user["email"], "ORDER_CONFIRMED", f"Order #{order.id} confirmed, total {order.total}")
    else:
        notify(user["email"], "PAYMENT_FAILED", f"Payment failed for order #{order.id}")
    return order_out(order)


@app.get("/orders")
def my_orders(user=Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Order).where(Order.user_id == user["id"]).order_by(Order.id.desc()))
    return [order_out(o) for o in rows]


@app.get("/orders/{order_id}")
def get_order(order_id: int, user=Depends(current_user), db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order or (order.user_id != user["id"] and user["role"] != "admin"):
        raise HTTPException(404, "Order not found")
    return order_out(order)


@app.put("/orders/{order_id}/status")
def update_status(order_id: int, body: StatusIn, _=Depends(require_admin),
                  db: Session = Depends(get_db)):
    status = body.status.upper()
    if status not in ADMIN_STATUSES:
        raise HTTPException(400, f"Status must be one of {sorted(ADMIN_STATUSES)}")
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    order.status = status
    db.commit()
    notify(order.user_email, f"ORDER_{status}", f"Order #{order.id} is now {status}")
    return order_out(order)
