from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    name: str
    password_hash: str
    role: str = "attendee"          # admin | staff | attendee


class Venue(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    location: str
    capacity: int
    description: str = ""
    image_url: str = ""
    hourly_rate: float = 0.0
    open_for_hire: bool = False


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    category: str
    venue_id: int
    starts_at: datetime
    ends_at: datetime
    description: str = ""
    status: str = "draft"           # draft | published | cancelled


class TicketCategory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: int = Field(index=True)
    name: str
    price: float
    quantity: int
    sold: int = 0


class Booking(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    event_id: int
    category_id: int
    quantity: int
    amount: float
    payment_ref: str
    status: str = "confirmed"       # confirmed | cancelled
    refund_amount: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Ticket(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    booking_id: int
    event_id: int
    code: str = Field(index=True, unique=True)
    status: str = "valid"           # valid | used | cancelled
    used_at: Optional[datetime] = None


class HireRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    venue_id: int
    starts_at: datetime
    ends_at: datetime
    purpose: str
    guests: int
    status: str = "pending"         # confirmed | escalated | rejected
    price: float = 0.0
    alternatives: str = ""          # JSON list
    decided_by: str = ""


class WaitlistEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    event_id: int
    category_id: int
    quantity: int
    status: str = "waiting"         # waiting | offered | expired | converted
    cycles: int = 0
    offered_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


class Notification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    title: str
    body: str
    read: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MessageLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: str = Field(index=True)
    sender: str
    receiver: str
    performative: str
    content: str                    # JSON string
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AgentState(SQLModel, table=True):
    agent: str = Field(primary_key=True)
    data: str = "{}"                # JSON beliefs
    updated_at: datetime = Field(default_factory=datetime.utcnow)