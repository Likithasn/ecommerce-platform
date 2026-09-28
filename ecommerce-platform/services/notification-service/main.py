import logging
from collections import deque

from fastapi import FastAPI
from pydantic import BaseModel

log = logging.getLogger("uvicorn.error")
app = FastAPI(title="Notification Service (logs instead of sending)")
recent = deque(maxlen=50)


class Notification(BaseModel):
    email: str
    event: str
    message: str


@app.get("/health")
def health():
    return {"status": "ok", "service": "notification"}


@app.post("/notify", status_code=202)
def notify(body: Notification):
    # Replace this log line with a SendGrid / Twilio call for real delivery.
    log.info("NOTIFY [%s] to=%s :: %s", body.event, body.email, body.message)
    recent.append(body.model_dump())
    return {"queued": True}


@app.get("/notifications")
def notifications():
    return list(recent)
