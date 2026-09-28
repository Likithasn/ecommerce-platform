import datetime as dt
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Float, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from database import Base, engine, get_db


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(index=True)
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20))
    card_last4: Mapped[str] = mapped_column(String(4))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))


class PayIn(BaseModel):
    order_id: int
    amount: float = Field(gt=0)
    card_number: str = "4242424242424242"


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Payment Service (mock gateway)", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": "payment"}


@app.post("/pay")
def pay(body: PayIn, db: Session = Depends(get_db)):
    """Mock processor: any card ending in 0000 is declined, everything else succeeds.
    Swap this function's body for a Stripe (test mode) call to go 'real'."""
    digits = "".join(ch for ch in body.card_number if ch.isdigit())
    ok = len(digits) >= 12 and not digits.endswith("0000")
    payment = Payment(order_id=body.order_id, amount=body.amount,
                      status="SUCCEEDED" if ok else "FAILED", card_last4=digits[-4:])
    db.add(payment)
    db.commit()
    return {"payment_id": payment.id, "status": payment.status}


@app.get("/payments/{order_id}")
def payments_for_order(order_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(Payment).where(Payment.order_id == order_id)).all()
    return [{"id": p.id, "amount": p.amount, "status": p.status} for p in rows]
