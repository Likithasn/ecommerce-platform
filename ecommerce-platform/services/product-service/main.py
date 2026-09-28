from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Float, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from auth import require_admin
from database import Base, engine, get_db


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str] = mapped_column(String(2000), default="")
    category: Mapped[str] = mapped_column(String(100), default="general", index=True)
    price: Mapped[float] = mapped_column(Float)
    stock: Mapped[int] = mapped_column(default=0)


class ProductIn(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    category: str = "general"
    price: float = Field(gt=0)
    stock: int = Field(default=0, ge=0)


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    price: Optional[float] = Field(default=None, gt=0)
    stock: Optional[int] = Field(default=None, ge=0)


class Line(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)


class Lines(BaseModel):
    items: list[Line]


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Product Catalog Service", lifespan=lifespan)


def product_out(p: Product):
    return {
        "id": p.id, "name": p.name, "description": p.description,
        "category": p.category, "price": p.price, "stock": p.stock,
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "product"}


@app.get("/products")
def list_products(category: Optional[str] = None, q: Optional[str] = None,
                  db: Session = Depends(get_db)):
    stmt = select(Product).order_by(Product.id)
    if category:
        stmt = stmt.where(Product.category == category)
    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q}%"))
    return [product_out(p) for p in db.scalars(stmt)]


@app.get("/products/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db)):
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(404, "Product not found")
    return product_out(p)


@app.post("/products", status_code=201)
def create_product(body: ProductIn, _=Depends(require_admin), db: Session = Depends(get_db)):
    p = Product(**body.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return product_out(p)


@app.put("/products/{product_id}")
def update_product(product_id: int, body: ProductUpdate, _=Depends(require_admin),
                   db: Session = Depends(get_db)):
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(404, "Product not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(p, field, value)
    db.commit()
    return product_out(p)


@app.delete("/products/{product_id}", status_code=204)
def delete_product(product_id: int, _=Depends(require_admin), db: Session = Depends(get_db)):
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(404, "Product not found")
    db.delete(p)
    db.commit()


# ---- internal endpoints: used by the order service, blocked at the gateway ----
@app.post("/products/internal/reserve")
def reserve(body: Lines, db: Session = Depends(get_db)):
    locked = {}
    for line in sorted(body.items, key=lambda l: l.product_id):
        p = db.scalar(select(Product).where(Product.id == line.product_id).with_for_update())
        if not p:
            raise HTTPException(404, f"Product {line.product_id} not found")
        if p.stock < line.quantity:
            raise HTTPException(409, f"Not enough stock for '{p.name}'")
        locked[p.id] = p
    for line in body.items:
        locked[line.product_id].stock -= line.quantity
    db.commit()
    return {"reserved": True}


@app.post("/products/internal/release")
def release(body: Lines, db: Session = Depends(get_db)):
    for line in body.items:
        p = db.get(Product, line.product_id)
        if p:
            p.stock += line.quantity
    db.commit()
    return {"released": True}
