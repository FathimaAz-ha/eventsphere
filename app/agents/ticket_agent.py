import json
import secrets
from datetime import datetime, timedelta

from sqlmodel import Session, select

from .bus import Message, MessageBus, make
from ..models import Ticket, Event, AgentState

CHECKIN_OPENS = timedelta(hours=2)
CHECKIN_CLOSES = timedelta(hours=1)


def _now() -> datetime:
    return datetime.utcnow()


class TicketAgent:
    name = "TicketAgent"
    goal = "Admit each ticket exactly once"

    def __init__(self, bus: MessageBus, engine):
        self.bus = bus
        self.engine = engine
        bus.subscribe(self.name, self.handle)

    async def handle(self, msg: Message):
        action = msg.content.get("action")
        try:
            if action == "issue":
                return self._issue(msg)
            if action == "check_in":
                return await self._check_in(msg)
        except Exception as e:
            return make(self.name, msg.sender, "FAILURE",
                        {"error": f"{type(e).__name__}: {e}"}, msg.conversation_id)
        return self._refuse(msg, f"Unknown action '{action}'")

    # ---------- helpers ----------
    def _refuse(self, msg, reason):
        return make(self.name, msg.sender, "REFUSE",
                    {"reason": reason}, msg.conversation_id)

    def _load_beliefs(self) -> dict:
        with Session(self.engine) as db:
            row = db.get(AgentState, self.name)
            return json.loads(row.data) if row else {}

    def _save_beliefs(self, data: dict):
        with Session(self.engine) as db:
            row = db.get(AgentState, self.name)
            if row:
                row.data = json.dumps(data)
                row.updated_at = _now()
            else:
                row = AgentState(agent=self.name, data=json.dumps(data))
            db.add(row)
            db.commit()

    # ---------- REQUEST: mint N unique codes ----------
    def _issue(self, msg: Message):
        c = msg.content
        codes = []
        with Session(self.engine) as db:
            for _ in range(int(c["quantity"])):
                code = "ES-" + secrets.token_urlsafe(18)
                db.add(Ticket(
                    booking_id=c["booking_id"],
                    event_id=c["event_id"],
                    code=code,
                ))
                codes.append(code)
            db.commit()
        return make(self.name, msg.sender, "AGREE",
                    {"codes": codes}, msg.conversation_id)

    # ---------- REQUEST: scan at the door ----------
    async def _check_in(self, msg: Message):
        c = msg.content
        code = (c.get("code") or "").strip()
        event_id = c.get("event_id")

        with Session(self.engine) as db:
            ticket = db.exec(
                select(Ticket).where(Ticket.code == code)).first()
            if not ticket:
                return self._refuse(msg, "Unknown ticket code")
            if ticket.status == "cancelled":
                return self._refuse(msg, "Ticket has been cancelled")
            if event_id and ticket.event_id != int(event_id):
                return self._refuse(msg, "Ticket is for a different event")
            if ticket.status == "used":
                # record a repeat-scan belief (possible shared screenshot)
                beliefs = self._load_beliefs()
                attempts = beliefs.setdefault("scan_attempts", {})
                attempts[code] = attempts.get(code, 0) + 1
                self._save_beliefs(beliefs)
                if attempts[code] >= 2:
                    await self.bus.send(make(
                        self.name, "*", "INFORM",
                        {"user_id": None,
                         "title": "Suspicious repeated scan",
                         "body": f"Code {code[:10]}… scanned {attempts[code]} "
                                 f"extra time(s) — possible screenshot sharing."},
                        msg.conversation_id,
                    ))
                return self._refuse(
                    msg, f"Already used at "
                         f"{ticket.used_at:%d %b %H:%M}"
                         f" — {attempts[code]} extra scan(s) recorded")

            event = db.get(Event, ticket.event_id)
            now = _now()
            if now < event.starts_at - CHECKIN_OPENS:
                return self._refuse(msg, "Too early — check-in opens 2h before start")
            if now > event.ends_at + CHECKIN_CLOSES:
                return self._refuse(msg, "Check-in window has closed")

            ticket.status = "used"
            ticket.used_at = now
            db.add(ticket)
            db.commit()
            tid, bid = ticket.id, ticket.booking_id

        return make(self.name, msg.sender, "AGREE",
                    {"status": "admitted", "ticket_id": tid, "booking_id": bid},
                    msg.conversation_id)