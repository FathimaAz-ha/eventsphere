import json
from datetime import datetime, timedelta
from sqlmodel import Session, select

from .bus import Message, MessageBus, make
from ..models import Venue, Event, AgentState, HireRequest

BUFFER = timedelta(minutes=30)
WEEKEND_MULTIPLIER = 1.2


class VenueAgent:
    name = "VenueAgent"
    goal = "Keep every venue's calendar free of clashes and coordinate allocation via FIPA Contract Net Protocol"

    def __init__(self, bus: MessageBus, engine):
        self.bus = bus
        self.engine = engine
        bus.subscribe(self.name, self.handle)
        self._sync_venue_participants()

    def _sync_venue_participants(self):
        """Register an autonomous participant agent on the bus for each venue in the database."""
        with Session(self.engine) as db:
            venues = db.exec(select(Venue)).all()
            for v in venues:
                agent_name = f"VenueAgent:{v.id}"
                if agent_name not in self.bus._handlers:
                    self.bus.subscribe(
                        agent_name,
                        lambda msg, vid=v.id: self._handle_venue_unit(msg, vid)
                    )

    # ---------- entry point ----------
    async def handle(self, msg: Message) -> Message:
        action = msg.content.get("action")
        if action == "check_slot":
            return self._check_slot(msg)
        if action == "hire_request":
            return await self._hire_request(msg)
        return self._refuse(msg, f"Unknown action '{action}'")

    # ---------- helpers ----------
    def _refuse(self, msg, reason, in_reply_to=None):
        return make(self.name, msg.sender, "REFUSE", {"reason": reason},
                    msg.conversation_id, protocol=msg.protocol, in_reply_to=in_reply_to or msg.id)

    def _propose(self, msg, reason, alternatives, in_reply_to=None):
        return make(self.name, msg.sender, "PROPOSE",
                    {"reason": reason, "alternatives": alternatives},
                    msg.conversation_id, protocol=msg.protocol, in_reply_to=in_reply_to or msg.id)

    def _clashes(self, db, venue_id, start, end, ignore_event_id=None, ignore_hire_id=None):
        q_events = select(Event).where(Event.venue_id == venue_id,
                                       Event.status == "published")
        for e in db.exec(q_events):
            if ignore_event_id and e.id == ignore_event_id:
                continue
            if start < e.ends_at + BUFFER and e.starts_at - BUFFER < end:
                return e

        q_hires = select(HireRequest).where(
            HireRequest.venue_id == venue_id,
            HireRequest.status.in_(["confirmed", "escalated"]),
        )
        for h in db.exec(q_hires):
            if ignore_hire_id and h.id == ignore_hire_id:
                continue
            if start < h.ends_at + BUFFER and h.starts_at - BUFFER < end:
                return h

        return None

    def _free_slots(self, db, venue_id, duration, after, want=3):
        events = db.exec(select(Event).where(Event.venue_id == venue_id,
                                             Event.status == "published")).all()
        hires = db.exec(select(HireRequest).where(
            HireRequest.venue_id == venue_id,
            HireRequest.status.in_(["confirmed", "escalated"]))).all()
        all_bookings = list(events) + list(hires)
        slots, t = [], max(after, datetime.utcnow())
        limit = t + timedelta(days=14)
        while t < limit and len(slots) < want:
            end = t + duration
            if not any(t < b.ends_at + BUFFER and b.starts_at - BUFFER < end for b in all_bookings):
                slots.append({"start": t.isoformat(), "end": end.isoformat()})
            t += timedelta(minutes=30)
        return slots

    def _price(self, venue, start, end):
        hours = (end - start).total_seconds() / 3600
        mult = WEEKEND_MULTIPLIER if start.weekday() >= 5 else 1.0
        return round(hours * venue.hourly_rate * mult, 2)

    # ---------- FIPA Iterated CNP: Participant Venue Unit Handler ----------
    async def _handle_venue_unit(self, msg: Message, venue_id: int) -> Message:
        c = msg.content
        agent_name = f"VenueAgent:{venue_id}"

        # 1. CFP received -> evaluate capability & submit bid
        if msg.performative == "CFP":
            start = datetime.fromisoformat(c["start"])
            end = datetime.fromisoformat(c["end"])
            guests = int(c.get("guests", 0))

            with Session(self.engine) as db:
                venue = db.get(Venue, venue_id)
                if not venue:
                    return make(agent_name, msg.sender, "REFUSE",
                                {"venue_id": venue_id, "reason": "Venue does not exist"},
                                msg.conversation_id, protocol="fipa-iterated-contract-net", in_reply_to=msg.id,
                                iteration=msg.iteration)
                if not venue.open_for_hire:
                    return make(agent_name, msg.sender, "REFUSE",
                                {"venue_id": venue_id, "reason": f"'{venue.name}' is not open for private hire"},
                                msg.conversation_id, protocol="fipa-iterated-contract-net", in_reply_to=msg.id,
                                iteration=msg.iteration)
                if guests > venue.capacity:
                    return make(agent_name, msg.sender, "REFUSE",
                                {"venue_id": venue_id, "capacity": venue.capacity,
                                 "reason": f"'{venue.name}' holds only {venue.capacity} guests"},
                                msg.conversation_id, protocol="fipa-iterated-contract-net", in_reply_to=msg.id,
                                iteration=msg.iteration)

                clash = self._clashes(db, venue.id, start, end)
                if clash:
                    return make(agent_name, msg.sender, "REFUSE",
                                {"venue_id": venue.id, "clash_title": clash.title,
                                 "reason": f"Venue booked by '{clash.title}' at that time"},
                                msg.conversation_id, protocol="fipa-iterated-contract-net", in_reply_to=msg.id,
                                iteration=msg.iteration)

                base_price = self._price(venue, start, end)

                # Round 2+ Counter-Offer handling:
                if c.get("action") == "counter_offer" or "counter_price" in c:
                    counter_price = float(c["counter_price"])
                    # Autonomous Concession Strategy:
                    # Venue allows up to 20% discount as its reservation floor
                    reserve_floor = round(base_price * 0.80, 2)

                    if counter_price >= base_price:
                        return make(agent_name, msg.sender, "PROPOSE",
                                    {"venue_id": venue.id, "venue_name": venue.name,
                                     "capacity": venue.capacity, "price": base_price,
                                     "original_price": base_price, "savings": 0.0,
                                     "concession": "Standard rate maintained",
                                     "is_final": False, "iteration": msg.iteration,
                                     "start": c["start"], "end": c["end"]},
                                    msg.conversation_id, protocol="fipa-iterated-contract-net", in_reply_to=msg.id,
                                    iteration=msg.iteration)
                    elif counter_price >= reserve_floor:
                        conceded_price = round(counter_price, 2)
                        savings = round(base_price - conceded_price, 2)
                        discount_pct = round((savings / base_price) * 100, 1)
                        return make(agent_name, msg.sender, "PROPOSE",
                                    {"venue_id": venue.id, "venue_name": venue.name,
                                     "capacity": venue.capacity, "price": conceded_price,
                                     "original_price": base_price, "savings": savings,
                                     "concession": f"Concession agreed: {discount_pct}% discount (-£{savings:.2f})",
                                     "is_final": conceded_price <= reserve_floor,
                                     "iteration": msg.iteration,
                                     "start": c["start"], "end": c["end"]},
                                    msg.conversation_id, protocol="fipa-iterated-contract-net", in_reply_to=msg.id,
                                    iteration=msg.iteration)
                    else:
                        # Below reserve floor: Offer Best-and-Final-Offer (BAFO) at the reserve floor
                        savings = round(base_price - reserve_floor, 2)
                        return make(agent_name, msg.sender, "PROPOSE",
                                    {"venue_id": venue.id, "venue_name": venue.name,
                                     "capacity": venue.capacity, "price": reserve_floor,
                                     "original_price": base_price, "savings": savings,
                                     "concession": f"Reserve floor reached: minimum rate is £{reserve_floor:.2f} (max 20% discount)",
                                     "is_final": True,
                                     "iteration": msg.iteration,
                                     "start": c["start"], "end": c["end"]},
                                    msg.conversation_id, protocol="fipa-iterated-contract-net", in_reply_to=msg.id,
                                    iteration=msg.iteration)

                # Round 1 Initial Bid:
                return make(agent_name, msg.sender, "PROPOSE",
                            {"venue_id": venue.id, "venue_name": venue.name,
                             "capacity": venue.capacity, "price": base_price,
                             "original_price": base_price, "savings": 0.0,
                             "iteration": msg.iteration,
                             "start": c["start"], "end": c["end"]},
                            msg.conversation_id, protocol="fipa-iterated-contract-net", in_reply_to=msg.id,
                            iteration=msg.iteration)

        # 2. ACCEPT-PROPOSAL received -> Award contract, evaluate escalation rules, confirm
        if msg.performative == "ACCEPT-PROPOSAL":
            start = datetime.fromisoformat(c["start"])
            end = datetime.fromisoformat(c["end"])
            duration = end - start
            guests = int(c.get("guests", 0))

            with Session(self.engine) as db:
                venue = db.get(Venue, venue_id)
                price = c.get("price", self._price(venue, start, end))
                original_price = c.get("original_price", price)
                savings = c.get("savings", round(original_price - price, 2))
                concession = c.get("concession", "")
                rounds = c.get("negotiation_rounds", msg.iteration)

                reasons = []
                if guests > venue.capacity * 0.8:
                    reasons.append("large booking (>80% capacity)")
                if start - datetime.utcnow() < timedelta(hours=24):
                    reasons.append("short notice (<24h)")
                if duration > timedelta(hours=6):
                    reasons.append("long duration (>6h)")

                if reasons:
                    return make(agent_name, msg.sender, "INFORM",
                                {"escalate": True, "reason": f"Escalated for admin approval: {', '.join(reasons)}",
                                 "price": price, "original_price": original_price, "savings": savings,
                                 "negotiation_rounds": rounds, "concession": concession,
                                 "venue_id": venue.id},
                                msg.conversation_id, protocol="fipa-iterated-contract-net", in_reply_to=msg.id,
                                iteration=msg.iteration)

                return make(agent_name, msg.sender, "INFORM",
                            {"escalate": False, "status": "confirmed", "price": price,
                             "original_price": original_price, "savings": savings,
                             "negotiation_rounds": rounds, "concession": concession,
                             "venue_id": venue.id, "start": c["start"], "end": c["end"]},
                            msg.conversation_id, protocol="fipa-iterated-contract-net", in_reply_to=msg.id,
                            iteration=msg.iteration)

        # 3. REJECT-PROPOSAL received -> Acknowledge bid release
        if msg.performative == "REJECT-PROPOSAL":
            return make(agent_name, msg.sender, "INFORM",
                        {"status": "released", "venue_id": venue_id},
                        msg.conversation_id, protocol="fipa-iterated-contract-net", in_reply_to=msg.id,
                        iteration=msg.iteration)

        return self._refuse(msg, f"Unknown performative '{msg.performative}' for venue unit")

    # ---------- REQUEST: can this event take this slot? ----------
    def _check_slot(self, msg: Message) -> Message:
        c = msg.content
        try:
            start = datetime.fromisoformat(c["start"])
            end = datetime.fromisoformat(c["end"])
        except (ValueError, KeyError, TypeError) as e:
            return self._refuse(msg, f"Invalid date format: {e}")

        if start < datetime.utcnow():
            return self._refuse(msg, "Event start time cannot be in the past")
        if end <= start:
            return self._refuse(msg, "End time must be after start time")

        with Session(self.engine) as db:
            venue = db.get(Venue, c["venue_id"])
            if not venue:
                return self._refuse(msg, f"Venue {c['venue_id']} does not exist")
            if c.get("total_tickets", 0) > venue.capacity:
                return self._refuse(
                    msg, f"Total tickets ({c['total_tickets']}) exceed venue "
                         f"capacity ({venue.capacity})")
            clash = self._clashes(db, venue.id, start, end, ignore_event_id=c.get("ignore_event_id"))
            if clash:
                return self._refuse(
                    msg, f"Clashes with '{clash.title}' "
                         f"({clash.starts_at:%d %b %H:%M}–{clash.ends_at:%H:%M}); "
                         f"venues need a {BUFFER.seconds // 60}-minute turnaround buffer")

        return make(self.name, msg.sender, "AGREE",
                    {"venue_id": c["venue_id"], "start": c["start"], "end": c["end"]},
                    msg.conversation_id)

    # ---------- FIPA CNP Orchestrator: private hire negotiation ----------
    async def _hire_request(self, msg: Message) -> Message:
        c = msg.content
        try:
            start = datetime.fromisoformat(c["start"])
            end = datetime.fromisoformat(c["end"])
        except (ValueError, KeyError, TypeError) as e:
            return self._refuse(msg, f"Invalid date format: {e}")

        now = datetime.utcnow()
        if start < now:
            return self._refuse(msg, "Booking start time cannot be in the past")
        if end <= start:
            return self._refuse(msg, "End time must be after start time")

        guests = int(c.get("guests", 0))
        if guests <= 0:
            return self._refuse(msg, "Number of guests must be at least 1")

        target_venue_id = int(c.get("venue_id", 0))
        target_budget = float(c["target_budget"]) if c.get("target_budget") is not None else None
        auto_negotiate = bool(c.get("auto_negotiate") or target_budget is not None)
        duration = end - start

        # Ensure all venue participants are active on the bus
        self._sync_venue_participants()

        # Step 1: Initiator broadcasts Round 1 CFP (Call for Proposals) to all venue units
        cfp_msg = make(
            sender=self.name,
            receiver="*",
            performative="CFP",
            content={
                "action": "evaluate_bid",
                "start": c["start"],
                "end": c["end"],
                "guests": guests,
                "purpose": c.get("purpose", ""),
                "target_venue_id": target_venue_id,
            },
            conversation_id=msg.conversation_id,
            protocol="fipa-iterated-contract-net",
            iteration=1,
        )
        replies = await self.bus.broadcast_collect(cfp_msg, target_prefix="VenueAgent:")

        # Step 2: Gathers and filters proposals & refusals
        proposals = [r for r in replies if r.performative == "PROPOSE"]
        refusals = [r for r in replies if r.performative == "REFUSE"]

        target_proposal = next((p for p in proposals if p.content.get("venue_id") == target_venue_id), None)
        target_refusal = next((r for r in refusals if r.content.get("venue_id") == target_venue_id), None)

        # Case A: Requested venue submitted a valid proposal
        if target_proposal:
            p1 = float(target_proposal.content["price"])
            winning_proposal = target_proposal
            rounds = 1
            concession_note = ""

            # Check if multi-turn counter-offer bargaining is triggered
            should_negotiate = (target_budget is not None and target_budget < p1) or (auto_negotiate and duration >= timedelta(hours=3)) or (auto_negotiate and target_budget is None)

            if should_negotiate:
                counter_price = target_budget if (target_budget is not None and target_budget < p1) else round(p1 * 0.85, 2)
                counter_cfp = make(
                    sender=self.name,
                    receiver=f"VenueAgent:{target_venue_id}",
                    performative="CFP",
                    content={
                        "action": "counter_offer",
                        "start": c["start"],
                        "end": c["end"],
                        "guests": guests,
                        "counter_price": counter_price,
                        "target_budget": target_budget,
                        "target_venue_id": target_venue_id,
                        "rationale": f"User target budget £{counter_price:.2f}" if target_budget else "Multi-hour duration discount requested",
                    },
                    conversation_id=msg.conversation_id,
                    protocol="fipa-iterated-contract-net",
                    in_reply_to=target_proposal.id,
                    iteration=2,
                )
                counter_reply = await self.bus.send(counter_cfp)
                if counter_reply and counter_reply.performative == "PROPOSE":
                    p2 = float(counter_reply.content["price"])
                    rounds = 2
                    concession_note = counter_reply.content.get("concession", "")

                    # If user had a strict target budget and target venue floor cannot meet it:
                    if target_budget is not None and p2 > target_budget:
                        budget_alts = [
                            p for p in proposals
                            if p.content.get("venue_id") != target_venue_id
                            and p.content.get("price", float("inf")) <= target_budget
                            and p.content.get("capacity", 0) >= guests
                        ]
                        if budget_alts:
                            alts_data = [{
                                "venue_id": p.content["venue_id"],
                                "venue_name": p.content["venue_name"],
                                "price": p.content["price"],
                                "start": c["start"],
                                "end": c["end"]
                            } for p in budget_alts]
                            await self.bus.send(make(
                                self.name, f"VenueAgent:{target_venue_id}", "REJECT-PROPOSAL",
                                {"venue_id": target_venue_id, "reason": "Target budget could not be met"},
                                msg.conversation_id, protocol="fipa-iterated-contract-net",
                                in_reply_to=counter_reply.id, iteration=2
                            ))
                            return self._propose(
                                msg,
                                f"Requested venue minimum rate is £{p2:.2f}, which exceeds your target budget of £{target_budget:.2f}. Alternate venues match your budget:",
                                alts_data,
                                in_reply_to=counter_reply.id
                            )

                    winning_proposal = counter_reply

            # Step 3: Send ACCEPT-PROPOSAL to the winning venue
            final_price = winning_proposal.content["price"]
            orig_price = winning_proposal.content.get("original_price", final_price)
            savings = winning_proposal.content.get("savings", round(orig_price - final_price, 2))

            accept_msg = make(
                sender=self.name,
                receiver=f"VenueAgent:{target_venue_id}",
                performative="ACCEPT-PROPOSAL",
                content={
                    "venue_id": target_venue_id,
                    "price": final_price,
                    "original_price": orig_price,
                    "savings": savings,
                    "concession": concession_note,
                    "negotiation_rounds": rounds,
                    "start": c["start"],
                    "end": c["end"],
                    "guests": guests,
                },
                conversation_id=msg.conversation_id,
                protocol="fipa-iterated-contract-net",
                in_reply_to=winning_proposal.id,
                iteration=rounds,
            )
            inform_reply = await self.bus.send(accept_msg)

            # Step 4: Send REJECT-PROPOSAL to all other bidders
            for other_p in proposals:
                if other_p.content.get("venue_id") != target_venue_id:
                    reject_msg = make(
                        sender=self.name,
                        receiver=other_p.sender,
                        performative="REJECT-PROPOSAL",
                        content={"venue_id": other_p.content.get("venue_id"), "reason": "Another venue selected"},
                        conversation_id=msg.conversation_id,
                        protocol="fipa-iterated-contract-net",
                        in_reply_to=other_p.id,
                        iteration=rounds,
                    )
                    await self.bus.send(reject_msg)

            # Step 5: Deliver outcome based on winner's INFORM response
            if inform_reply and inform_reply.content.get("escalate"):
                return make(self.name, msg.sender, "PROPOSE",
                            {"reason": inform_reply.content["reason"],
                             "escalate": True,
                             "price": final_price,
                             "original_price": orig_price,
                             "savings": savings,
                             "negotiation_rounds": rounds,
                             "concession": concession_note,
                             "alternatives": []},
                            msg.conversation_id, iteration=rounds)

            return make(self.name, msg.sender, "AGREE",
                        {"price": final_price,
                         "original_price": orig_price,
                         "savings": savings,
                         "negotiation_rounds": rounds,
                         "concession": concession_note,
                         "venue_id": target_venue_id,
                         "start": c["start"], "end": c["end"]},
                        msg.conversation_id, iteration=rounds)

        # Case B: Requested venue refused
        with Session(self.engine) as db:
            venue = db.get(Venue, target_venue_id)
            if not venue:
                return self._refuse(msg, f"Venue {target_venue_id} does not exist")

            # Refused because of capacity
            if guests > venue.capacity:
                if not proposals:
                    return self._refuse(msg, f"No venue can hold {guests} guests")
                alts = [{"venue_id": p.content["venue_id"], "venue_name": p.content["venue_name"],
                         "start": c["start"], "end": c["end"]} for p in proposals[:3]]
                return self._propose(
                    msg,
                    f"'{venue.name}' holds only {venue.capacity} guests",
                    alts)

            # Refused because of clash
            clash = self._clashes(db, venue.id, start, end)
            if clash:
                alts = [{"venue_id": venue.id, "venue_name": venue.name, **s}
                        for s in self._free_slots(db, venue.id, duration, start)]
                return self._propose(
                    msg,
                    f"Venue booked by '{clash.title}' at that time",
                    alts)

            # Refused because not open for hire or other reason
            reason = target_refusal.content.get("reason", "Venue not available for hire") if target_refusal else f"'{venue.name}' is not open for private hire"
            return self._refuse(msg, reason)
