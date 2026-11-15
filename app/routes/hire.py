import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import HireRequest, User
from ..security import require_user, require_role
from ..agents.bus import make

router = APIRouter(prefix="/api/hire", tags=["hire"])


class HireIn(BaseModel):
    venue_id: int
    starts_at: datetime
    ends_at: datetime
    purpose: str
    guests: int
    target_budget: float | None = None
    auto_negotiate: bool | None = False


class DecideIn(BaseModel):
    approve: bool


@router.post("")
async def request_hire(body: HireIn, request: Request,
                       user: User = Depends(require_user),
                       db: Session = Depends(get_session)):
    conv = uuid.uuid4().hex
    reply = await request.app.state.bus.send(make(
        "HireRoute", "VenueAgent", "REQUEST",
        {"action": "hire_request", "venue_id": body.venue_id,
         "start": body.starts_at.isoformat(), "end": body.ends_at.isoformat(),
         "purpose": body.purpose, "guests": body.guests,
         "target_budget": body.target_budget,
         "auto_negotiate": body.auto_negotiate},
        conv,
    ))
    if not reply:
        raise HTTPException(500, "No reply from VenueAgent")

    if reply.performative == "REFUSE":
        h = HireRequest(user_id=user.id, venue_id=body.venue_id,
                        starts_at=body.starts_at, ends_at=body.ends_at,
                        purpose=body.purpose, guests=body.guests,
                        status="rejected", decided_by="VenueAgent")
        db.add(h); db.commit()
        raise HTTPException(409, reply.content.get("reason", "Refused"))

    c = reply.content
    if reply.performative == "PROPOSE":
        status = "escalated" if c.get("escalate") else "pending"
        alts = c.get("alternatives", [])
    else:  # AGREE
        status, alts = "confirmed", []

    price = c.get("price", 0)
    orig_price = c.get("original_price", price)
    savings = c.get("savings", round(orig_price - price, 2))
    rounds = c.get("negotiation_rounds", 1)
    concession = c.get("concession", "")

    h = HireRequest(user_id=user.id, venue_id=body.venue_id,
                    starts_at=body.starts_at, ends_at=body.ends_at,
                    purpose=body.purpose, guests=body.guests,
                    status=status, price=price,
                    original_price=orig_price, savings=savings,
                    negotiation_rounds=rounds, concession_note=concession,
                    alternatives=json.dumps(alts),
                    decided_by="VenueAgent")
    db.add(h); db.commit(); db.refresh(h)
    return {"status": status, "hire_id": h.id,
            "price": price, "original_price": orig_price,
            "savings": savings, "negotiation_rounds": rounds,
            "concession": concession, "reason": c.get("reason"),
            "alternatives": alts, "conversation_id": conv}


@router.get("/mine")
def my_hires(user: User = Depends(require_user),
             db: Session = Depends(get_session)):
    rows = db.exec(select(HireRequest).where(HireRequest.user_id == user.id)
                   .order_by(HireRequest.id.desc())).all()
    return [{"id": h.id, "venue_id": h.venue_id, "status": h.status,
             "price": h.price, "guests": h.guests, "purpose": h.purpose,
             "starts_at": h.starts_at.isoformat(),
             "ends_at": h.ends_at.isoformat(),
             "decided_by": h.decided_by} for h in rows]


@router.get("/pending")
def pending_hires(admin: User = Depends(require_role("admin")),
                  db: Session = Depends(get_session)):
    rows = db.exec(select(HireRequest)
                   .where(HireRequest.status == "escalated")
                   .order_by(HireRequest.id)).all()
    return [{"id": h.id, "user_id": h.user_id, "venue_id": h.venue_id,
             "price": h.price, "guests": h.guests, "purpose": h.purpose,
             "starts_at": h.starts_at.isoformat(),
             "ends_at": h.ends_at.isoformat()} for h in rows]


@router.post("/{hire_id}/decide")
def decide_hire(hire_id: int, body: DecideIn, request: Request,
                admin: User = Depends(require_role("admin")),
                db: Session = Depends(get_session)):
    h = db.get(HireRequest, hire_id)
    if not h:
        raise HTTPException(404, "Hire request not found")
    if h.status != "escalated":
        raise HTTPException(400, "Not pending admin decision")
    if body.approve:
        venue_agent = getattr(request.app.state, "agents", {}).get("VenueAgent")
        if venue_agent:
            clash = venue_agent._clashes(db, h.venue_id, h.starts_at, h.ends_at, ignore_hire_id=h.id)
            if clash:
                raise HTTPException(409, f"Cannot approve: clashes with '{clash.title}'")
        h.status = "confirmed"
    else:
        h.status = "rejected"
    h.decided_by = f"admin:{admin.email}"
    db.add(h); db.commit()
    return {"ok": True, "status": h.status}