import base64
import io
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import qrcode
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from ..db import get_session
from ..models import (
    User, Venue, Event, TicketCategory, Booking, Ticket,
    HireRequest, MessageLog,
)
from ..security import (
    current_user, current_session, require_user, require_role,
    verify_password, hash_password, create_session, destroy_session,
    SESSION_COOKIE, SESSION_DAYS,
)
from ..agents.bus import make

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates"))


# ---------- helpers ----------

def _flash(request: Request):
    msg = request.query_params.get("msg")
    if not msg:
        return None
    return {"msg": msg, "type": request.query_params.get("type", "info")}


def _ctx(request, user, sess, **extra):
    return {"request": request, "user": user,
            "csrf": sess.csrf if sess else None,
            "flash": _flash(request), **extra}


def _redirect(url: str, msg: str, type: str = "info") -> RedirectResponse:
    return RedirectResponse(f"{url}?msg={quote(msg)}&type={type}", status_code=303)


def _csrf_or_403(sess, token: str):
    if not sess or sess.csrf != token:
        raise HTTPException(403, "Invalid CSRF token")


def _qr(text: str) -> str:
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ---------- auth pages ----------

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request,
               user=Depends(current_user), sess=Depends(current_session)):
    if user:
        return RedirectResponse("/events", status_code=303)
    return templates.TemplateResponse(request, "login.html", _ctx(request, user, sess))


@router.post("/login")
def login_post(email: str = Form(...), password: str = Form(...),
               db: Session = Depends(get_session)):
    u = db.exec(select(User).where(User.email == email)).first()
    if not u or not verify_password(password, u.password_hash):
        return _redirect("/login", "Invalid email or password", "danger")
    tok = create_session(db, u.id)
    resp = RedirectResponse("/events", status_code=303)
    resp.set_cookie(SESSION_COOKIE, tok.id, httponly=True,
                    samesite="strict", max_age=SESSION_DAYS * 24 * 3600)
    return resp


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request,
                  user=Depends(current_user), sess=Depends(current_session)):
    if user:
        return RedirectResponse("/events", status_code=303)
    return templates.TemplateResponse(request, "register.html", _ctx(request, user, sess))


@router.post("/register")
def register_post(email: str = Form(...), name: str = Form(...),
                  password: str = Form(...),
                  db: Session = Depends(get_session)):
    if db.exec(select(User).where(User.email == email)).first():
        return _redirect("/register", "Email already registered", "danger")
    if len(password) < 6:
        return _redirect("/register", "Password must be at least 6 chars", "danger")
    u = User(email=email, name=name,
             password_hash=hash_password(password), role="attendee")
    db.add(u); db.commit(); db.refresh(u)
    tok = create_session(db, u.id)
    resp = RedirectResponse("/events", status_code=303)
    resp.set_cookie(SESSION_COOKIE, tok.id, httponly=True,
                    samesite="strict", max_age=SESSION_DAYS * 24 * 3600)
    return resp


@router.get("/logout")
def logout(db: Session = Depends(get_session),
           sess=Depends(current_session)):
    if sess:
        destroy_session(db, sess.id)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ---------- events ----------

@router.get("/events", response_class=HTMLResponse)
def events_page(request: Request, q: str = "", category: str = "",
                user=Depends(current_user), sess=Depends(current_session),
                db: Session = Depends(get_session)):
    events = db.exec(select(Event).where(Event.status == "published")
                     .order_by(Event.starts_at)).all()
    if q:
        ql = q.lower()
        events = [e for e in events
                  if ql in e.title.lower() or ql in e.description.lower()]
    if category:
        events = [e for e in events if e.category == category]
    return templates.TemplateResponse(request, "events.html",
        _ctx(request, user, sess, events=events, q=q, category=category))


@router.get("/events/{event_id}", response_class=HTMLResponse)
def event_page(event_id: int, request: Request,
               user=Depends(current_user), sess=Depends(current_session),
               db: Session = Depends(get_session)):
    e = db.get(Event, event_id)
    if not e:
        raise HTTPException(404, "Event not found")
    cats = db.exec(select(TicketCategory)
                   .where(TicketCategory.event_id == event_id)).all()
    return templates.TemplateResponse(request, "event_detail.html",
        _ctx(request, user, sess, event=e, categories=cats))


# ---------- bookings ----------

@router.post("/bookings/create")
async def booking_create(request: Request,
                         category_id: int = Form(...),
                         quantity: int = Form(...),
                         csrf: str = Form(""),
                         user=Depends(require_user),
                         sess=Depends(current_session)):
    _csrf_or_403(sess, csrf)
    reply = await request.app.state.bus.send(make(
        "WebUI", "BookingAgent", "REQUEST",
        {"action": "book", "user_id": user.id,
         "category_id": category_id, "quantity": quantity},
        uuid.uuid4().hex))
    if not reply or reply.performative != "AGREE":
        reason = (reply.content.get("reason")
                  or reply.content.get("error")
                  or "Booking refused") if reply else "No reply"
        return _redirect("/events", reason, "danger")
    return _redirect("/tickets",
                     f"Booked. Paid £{reply.content['amount']:.2f}.", "success")


@router.get("/bookings", response_class=HTMLResponse)
def bookings_page(request: Request,
                  user=Depends(require_user), sess=Depends(current_session),
                  db: Session = Depends(get_session)):
    rows = db.exec(select(Booking).where(Booking.user_id == user.id)
                   .order_by(Booking.id.desc())).all()
    out = []
    for b in rows:
        e = db.get(Event, b.event_id)
        c = db.get(TicketCategory, b.category_id)
        out.append({"id": b.id,
                    "event_title": e.title if e else "?",
                    "category_name": c.name if c else "?",
                    "quantity": b.quantity, "status": b.status,
                    "payment_ref": b.payment_ref,
                    "refund_amount": b.refund_amount})
    return templates.TemplateResponse(request, "my_bookings.html",
        _ctx(request, user, sess, bookings=out))


@router.post("/bookings/{booking_id}/cancel")
async def booking_cancel(booking_id: int, request: Request,
                         csrf: str = Form(""),
                         user=Depends(require_user),
                         sess=Depends(current_session),
                         db: Session = Depends(get_session)):
    _csrf_or_403(sess, csrf)
    b = db.get(Booking, booking_id)
    if not b or b.user_id != user.id:
        raise HTTPException(404, "Booking not found")
    reply = await request.app.state.bus.send(make(
        "WebUI", "BookingAgent", "REQUEST",
        {"action": "cancel", "booking_id": booking_id},
        uuid.uuid4().hex))
    if not reply or reply.performative != "AGREE":
        reason =  (reply.content.get("reason")
                  or reply.content.get("error")
                  or "Cancel refused") if reply else "No reply"
        return _redirect("/bookings", reason, "danger")
    refund = reply.content.get("refund", 0)
    return _redirect("/bookings", f"Cancelled. Refund £{refund:.2f}.", "success")


# ---------- tickets ----------

@router.get("/tickets", response_class=HTMLResponse)
def tickets_page(request: Request,
                 user=Depends(require_user), sess=Depends(current_session),
                 db: Session = Depends(get_session)):
    bookings = db.exec(select(Booking).where(
        Booking.user_id == user.id, Booking.status == "confirmed")).all()
    out = []
    for b in bookings:
        for t in db.exec(select(Ticket)
                         .where(Ticket.booking_id == b.id)).all():
            e = db.get(Event, t.event_id)
            out.append({"code": t.code, "status": t.status,
                        "event_title": e.title if e else "?",
                        "qr": _qr(t.code)})
    return templates.TemplateResponse(request, "my_tickets.html",
        _ctx(request, user, sess, tickets=out))


# ---------- scan ----------

@router.get("/scan", response_class=HTMLResponse)
def scan_page(request: Request,
              user=Depends(require_role("staff", "admin")),
              sess=Depends(current_session)):
    return templates.TemplateResponse(request, "scan.html", _ctx(request, user, sess))


# ---------- hire ----------

def _hire_page(request, user, sess, db, result=None):
    venues = db.exec(select(Venue).where(Venue.open_for_hire == True)).all()
    hires = db.exec(select(HireRequest).where(HireRequest.user_id == user.id)
                    .order_by(HireRequest.id.desc())).all()
    return templates.TemplateResponse(request, "hire_form.html",
        _ctx(request, user, sess, venues=venues, hires=hires, result=result))


@router.get("/hire", response_class=HTMLResponse)
def hire_get(request: Request,
             user=Depends(require_user), sess=Depends(current_session),
             db: Session = Depends(get_session)):
    return _hire_page(request, user, sess, db)


@router.post("/hire/request", response_class=HTMLResponse)
async def hire_post(request: Request,
                    venue_id: int = Form(...), starts_at: str = Form(...),
                    ends_at: str = Form(...), purpose: str = Form(...),
                    guests: int = Form(...), csrf: str = Form(""),
                    user=Depends(require_user),
                    sess=Depends(current_session),
                    db: Session = Depends(get_session)):
    _csrf_or_403(sess, csrf)
    reply = await request.app.state.bus.send(make(
        "WebUI", "VenueAgent", "REQUEST",
        {"action": "hire_request", "venue_id": venue_id,
         "start": starts_at, "end": ends_at,
         "purpose": purpose, "guests": guests},
        uuid.uuid4().hex))
    if not reply:
        raise HTTPException(500, "VenueAgent did not reply")

    c = reply.content
    start = datetime.fromisoformat(starts_at)
    end = datetime.fromisoformat(ends_at)

    if reply.performative == "REFUSE":
        result = {"status": "refused", "reason": c.get("reason"),
                  "price": 0, "alternatives": []}
    elif reply.performative == "PROPOSE":
        escalate = bool(c.get("escalate"))
        result = {"status": "escalated" if escalate else "pending",
                  "reason": c.get("reason"),
                  "price": c.get("price", 0),
                  "alternatives": c.get("alternatives", [])}
        db.add(HireRequest(user_id=user.id, venue_id=venue_id,
                           starts_at=start, ends_at=end,
                           purpose=purpose, guests=guests,
                           status="escalated" if escalate else "pending",
                           price=c.get("price", 0),
                           alternatives=str(c.get("alternatives", [])),
                           decided_by="VenueAgent"))
        db.commit()
    else:  # AGREE
        result = {"status": "confirmed",
                  "reason": "Confirmed automatically by VenueAgent",
                  "price": c.get("price", 0), "alternatives": []}
        db.add(HireRequest(user_id=user.id, venue_id=venue_id,
                           starts_at=start, ends_at=end,
                           purpose=purpose, guests=guests,
                           status="confirmed", price=c.get("price", 0),
                           decided_by="VenueAgent"))
        db.commit()

    return _hire_page(request, user, sess, db, result=result)


# ---------- dashboard (admin) ----------

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request,
                   user=Depends(require_role("admin")),
                   sess=Depends(current_session),
                   db: Session = Depends(get_session)):
    events = db.exec(select(Event).order_by(Event.starts_at.desc())).all()
    rows = []
    for e in events:
        cats = db.exec(select(TicketCategory)
                       .where(TicketCategory.event_id == e.id)).all()
        rev = sum(b.amount for b in db.exec(select(Booking).where(
            Booking.event_id == e.id, Booking.status == "confirmed")).all())
        ins = len(db.exec(select(Ticket).where(
            Ticket.event_id == e.id, Ticket.status == "used")).all())
        rows.append({"title": e.title, "status": e.status,
                     "sold": sum(c.sold for c in cats),
                     "capacity": sum(c.quantity for c in cats),
                     "revenue": rev, "check_ins": ins})
    hires = db.exec(select(HireRequest)
                    .where(HireRequest.status == "escalated")).all()
    msgs = db.exec(select(MessageLog)
                   .order_by(MessageLog.id.desc()).limit(50)).all()
    return templates.TemplateResponse(request, "dashboard.html",
        _ctx(request, user, sess, rows=rows, hires=hires, messages=msgs))


@router.post("/dashboard/hire/{hire_id}/approve")
def hire_approve(hire_id: int, csrf: str = Form(""),
                 user=Depends(require_role("admin")),
                 sess=Depends(current_session),
                 db: Session = Depends(get_session)):
    _csrf_or_403(sess, csrf)
    h = db.get(HireRequest, hire_id)
    if h and h.status == "escalated":
        h.status = "confirmed"
        h.decided_by = f"admin:{user.email}"
        db.add(h); db.commit()
    return _redirect("/dashboard", "Approved", "success")


@router.post("/dashboard/hire/{hire_id}/reject")
def hire_reject(hire_id: int, csrf: str = Form(""),
                user=Depends(require_role("admin")),
                sess=Depends(current_session),
                db: Session = Depends(get_session)):
    _csrf_or_403(sess, csrf)
    h = db.get(HireRequest, hire_id)
    if h and h.status == "escalated":
        h.status = "rejected"
        h.decided_by = f"admin:{user.email}"
        db.add(h); db.commit()
    return _redirect("/dashboard", "Rejected", "warning")


# ---------- admin: venue & event creation ----------

@router.get("/admin/venue/new", response_class=HTMLResponse)
def new_venue_page(request: Request,
                   user=Depends(require_role("admin")),
                   sess=Depends(current_session)):
    return templates.TemplateResponse(request, "new_venue.html",
                                      _ctx(request, user, sess))


@router.post("/admin/venue")
def new_venue_post(name: str = Form(...), location: str = Form(...),
                   capacity: int = Form(...), description: str = Form(""),
                   hourly_rate: float = Form(0.0),
                   open_for_hire: int = Form(0),
                   csrf: str = Form(""),
                   user=Depends(require_role("admin")),
                   sess=Depends(current_session),
                   db: Session = Depends(get_session)):
    _csrf_or_403(sess, csrf)
    if capacity <= 0:
        return _redirect("/admin/venue/new", "Capacity must be positive", "danger")
    db.add(Venue(name=name, location=location, capacity=capacity,
                 description=description, hourly_rate=hourly_rate,
                 open_for_hire=bool(open_for_hire)))
    db.commit()
    return _redirect("/dashboard", f"Venue '{name}' created", "success")


@router.get("/admin/event/new", response_class=HTMLResponse)
def new_event_page(request: Request,
                   user=Depends(require_role("admin")),
                   sess=Depends(current_session),
                   db: Session = Depends(get_session)):
    venues = db.exec(select(Venue)).all()
    return templates.TemplateResponse(request, "new_event.html",
        _ctx(request, user, sess, venues=venues))


@router.post("/admin/event")
async def new_event_post(request: Request,
                         title: str = Form(...), category: str = Form(...),
                         venue_id: int = Form(...), starts_at: str = Form(...),
                         ends_at: str = Form(...), description: str = Form(""),
                         ticket_name: str = Form("Standard"),
                         ticket_price: float = Form(0.0),
                         ticket_quantity: int = Form(...),
                         csrf: str = Form(""),
                         user=Depends(require_role("admin")),
                         sess=Depends(current_session),
                         db: Session = Depends(get_session)):
    _csrf_or_403(sess, csrf)
    venue = db.get(Venue, venue_id)
    if not venue:
        return _redirect("/admin/event/new", "Venue not found", "danger")
    if ticket_quantity > venue.capacity:
        return _redirect("/admin/event/new",
                         f"Tickets ({ticket_quantity}) exceed venue capacity ({venue.capacity})",
                         "danger")

    start = datetime.fromisoformat(starts_at)
    end = datetime.fromisoformat(ends_at)

    reply = await request.app.state.bus.send(make(
        "WebUI", "VenueAgent", "REQUEST",
        {"action": "check_slot", "venue_id": venue_id,
         "start": start.isoformat(), "end": end.isoformat(),
         "total_tickets": ticket_quantity},
        uuid.uuid4().hex))
    if not reply or reply.performative != "AGREE":
        reason = reply.content.get("reason", "Refused by VenueAgent") if reply else "No reply"
        return _redirect("/admin/event/new", reason, "danger")

    e = Event(title=title, category=category, venue_id=venue_id,
              starts_at=start, ends_at=end, description=description,
              status="published")
    db.add(e); db.commit(); db.refresh(e)
    db.add(TicketCategory(event_id=e.id, name=ticket_name,
                          price=ticket_price, quantity=ticket_quantity))
    db.commit()
    return _redirect("/dashboard", f"Event '{title}' published", "success")