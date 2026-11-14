from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import Venue, User
from ..security import require_role

router = APIRouter(prefix="/api/venues", tags=["venues"])


class VenueIn(BaseModel):
    name: str
    location: str
    capacity: int
    description: str = ""
    image_url: str = ""
    hourly_rate: float = 0.0
    open_for_hire: bool = False


def _dict(v: Venue) -> dict:
    return {"id": v.id, "name": v.name, "location": v.location,
            "capacity": v.capacity, "description": v.description,
            "image_url": v.image_url, "hourly_rate": v.hourly_rate,
            "open_for_hire": v.open_for_hire}


@router.get("")
def list_venues(db: Session = Depends(get_session)):
    return [_dict(v) for v in db.exec(select(Venue)).all()]


@router.post("")
def create_venue(body: VenueIn,
                 admin: User = Depends(require_role("admin")),
                 db: Session = Depends(get_session)):
    if body.capacity <= 0:
        raise HTTPException(400, "Capacity must be positive")
    v = Venue(**body.model_dump())
    db.add(v); db.commit(); db.refresh(v)
    return _dict(v)


@router.patch("/{venue_id}")
def update_venue(venue_id: int, body: VenueIn,
                 admin: User = Depends(require_role("admin")),
                 db: Session = Depends(get_session)):
    v = db.get(Venue, venue_id)
    if not v:
        raise HTTPException(404, "Venue not found")
    for k, val in body.model_dump().items():
        setattr(v, k, val)
    db.add(v); db.commit(); db.refresh(v)
    return _dict(v)