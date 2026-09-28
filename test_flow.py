"""End-to-end test against the running stack (through the API gateway)."""
import os
import uuid

import pytest
import requests

BASE = os.getenv("BASE_URL", "http://localhost:8080")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
PASSWORD = "secret123"


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def register_and_login(email):
    r = requests.post(f"{BASE}/api/users/register",
                      json={"email": email, "password": PASSWORD, "full_name": "Test"})
    assert r.status_code in (201, 409), r.text
    r = requests.post(f"{BASE}/api/users/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def admin():
    return register_and_login(ADMIN_EMAIL)


@pytest.fixture()
def customer():
    return register_and_login(f"user-{uuid.uuid4().hex[:8]}@example.com")


@pytest.fixture()
def product(admin):
    r = requests.post(f"{BASE}/api/products", headers=auth(admin), json={
        "name": f"Widget {uuid.uuid4().hex[:6]}", "category": "gadgets", "price": 25.5, "stock": 10})
    assert r.status_code == 201, r.text
    return r.json()


def stock_of(pid):
    return requests.get(f"{BASE}/api/products/{pid}").json()["stock"]


def test_gateway_health():
    assert requests.get(f"{BASE}/health").status_code == 200


def test_customer_cannot_create_product(customer):
    r = requests.post(f"{BASE}/api/products", headers=auth(customer),
                      json={"name": "x", "price": 1, "stock": 1})
    assert r.status_code == 403


def test_internal_endpoints_are_blocked():
    r = requests.post(f"{BASE}/api/products/internal/reserve", json={"items": []})
    assert r.status_code == 403


def test_full_purchase_flow(customer, product):
    pid = product["id"]
    r = requests.post(f"{BASE}/api/cart/items", headers=auth(customer),
                      json={"product_id": pid, "quantity": 2})
    assert r.status_code == 201, r.text
    assert r.json()["total"] == 51.0

    r = requests.post(f"{BASE}/api/orders", headers=auth(customer), json={})
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["status"] == "PAID"
    assert order["total"] == 51.0

    assert stock_of(pid) == 8                                                  # stock decremented
    assert requests.get(f"{BASE}/api/cart", headers=auth(customer)).json()["items"] == []
    history = requests.get(f"{BASE}/api/orders", headers=auth(customer)).json()
    assert [o["id"] for o in history] == [order["id"]]


def test_declined_card_restores_stock(customer, product):
    pid = product["id"]
    requests.post(f"{BASE}/api/cart/items", headers=auth(customer),
                  json={"product_id": pid, "quantity": 3})
    r = requests.post(f"{BASE}/api/orders", headers=auth(customer),
                      json={"card_number": "4000000000000000"})
    assert r.status_code == 201
    assert r.json()["status"] == "PAYMENT_FAILED"
    assert stock_of(pid) == 10


def test_cannot_add_more_than_stock(customer, product):
    r = requests.post(f"{BASE}/api/cart/items", headers=auth(customer),
                      json={"product_id": product["id"], "quantity": 999})
    assert r.status_code == 409


def test_admin_can_ship_order(customer, admin, product):
    requests.post(f"{BASE}/api/cart/items", headers=auth(customer),
                  json={"product_id": product["id"], "quantity": 1})
    oid = requests.post(f"{BASE}/api/orders", headers=auth(customer), json={}).json()["id"]
    r = requests.put(f"{BASE}/api/orders/{oid}/status", headers=auth(admin),
                     json={"status": "shipped"})
    assert r.status_code == 200 and r.json()["status"] == "SHIPPED"
    r = requests.put(f"{BASE}/api/orders/{oid}/status", headers=auth(customer),
                     json={"status": "shipped"})
    assert r.status_code == 403
