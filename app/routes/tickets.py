import base64
import io
import uuid

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import Booking, Event, Ticket, User
from ..security import require_user, require_role
from ..agents.bus import make

router = APIRouter(prefix="/api/tickets", tags=["tickets"])
scan_router = APIRouter(prefix="/api/scan", tags=["scan"])


def _qr_data_url(text: str) -> str:
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@router.get("/mine")
def my_tickets(user: User = Depends(require_user),
               db: Session = Depends(get_session)):
    bookings = db.exec(select(Booking).where(
        Booking.user_id == user.id, Booking.status == "confirmed")).all()
    out = []
    for b in bookings:
        for t in db.exec(select(Ticket).where(Ticket.booking_id == b.id)).all():
            e = db.get(Event, t.event_id)
            out.append({"ticket_id": t.id, "code": t.code,
                        "event_id": t.event_id,
                        "event_title": e.title if e else "?",
                        "status": t.status,
                        "qr": _qr_data_url(t.code)})
    return out


class ScanIn(BaseModel):
    code: str
    event_id: int | None = None


@scan_router.post("")
async def scan(body: ScanIn, request: Request,
               staff: User = Depends(require_role("staff", "admin")),
               db: Session = Depends(get_session)):
    conv = uuid.uuid4().hex
    reply = await request.app.state.bus.send(make(
        "ScanRoute", "TicketAgent", "REQUEST",
        {"action": "check_in", "code": body.code, "event_id": body.event_id},
        conv,
    ))
    if not reply:
        raise HTTPException(500, "No reply from TicketAgent")
    return {"performative": reply.performative,
            "conversation_id": conv, **reply.content}