from sqlmodel import Session

from .bus import Message, MessageBus
from ..models import Notification


class NotificationAgent:
    name = "NotificationAgent"
    goal = "Tell the right person what affects them"

    def __init__(self, bus: MessageBus, engine):
        self.bus = bus
        self.engine = engine
        # listener: reacts to any INFORM in the system
        
        bus.subscribe("*", self.handle)

    async def handle(self, msg: Message):
        if msg.performative != "INFORM":
            return None
        c = msg.content
        user_id = c.get("user_id")
        if not user_id:
            return None
        with Session(self.engine) as db:
            db.add(Notification(
                user_id=int(user_id),
                title=c.get("title", "Update"),
                body=c.get("body", ""),
            ))
            db.commit()
        return None