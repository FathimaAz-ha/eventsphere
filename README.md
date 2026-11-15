# EventSphere — Multi-Agent Event & Venue Management Platform

> **Module**: IT4123 Agent-Based Computing (ICA02)  
> **Student**: Fathima Azha (2021ICT48)  
> **Architecture**: Decentralized Multi-Agent System (MAS) compliant with FIPA ACL standards  
> **Frameworks**: FastAPI, SQLModel (SQLite / PostgreSQL), Pydantic v2, Jinja2 (Glassmorphism Mobile-First UI)

---

## 1. System Overview

EventSphere is an autonomous multi-agent platform designed for event ticketing, venue scheduling, dynamic private hire bidding, and fraud prevention. The platform coordinates five autonomous agents communicating over an in-process asynchronous message bus using **FIPA ACL** performatives:

1. **VenueAgent**: Manages venue calendars, enforces turnaround buffers, and orchestrates decentralized **FIPA Contract Net Protocol (SC00029H)** and **FIPA Iterated Multi-Turn Bargaining (SC00030H)** across independent venue units (`VenueAgent:{id}`).
2. **BookingAgent**: Manages ticket inventories, calculates tiered cancellation refunds (100% / 50% / 0%), and prevents concurrent overbooking race conditions.
3. **TicketAgent**: Generates cryptographically unique tickets (`ES-XXXXXX`), produces scannable QR codes, and prevents double check-ins.
4. **WaitlistAgent**: Runs autonomous background sweeps to expire unredeemed ticket reservations and reoffers them to waitlisted attendees.
5. **NotificationAgent**: Subscribes to broadcast channels (`*`) to persist and dispatch user alert notifications.

---

## 2. Live Deployment & GitHub Repository

- **Live HTTPS URL**: `https://eventsphere-production-25cbbf3d.up.railway.app/events`
- **GitHub Repository**: `https://github.com/FathimaAz-ha/eventsphere`

---

## 3. Demo Accounts (Idempotent Viva Seed Data)

The database automatically seeds the three required testing accounts upon startup:

| Role | Email Address | Password | Permissions & Capabilities |
| :--- | :--- | :--- | :--- |
| **Admin** | `admin@eventsphere.local` | `admin123` | Full dashboard metrics, hire approval/rejection, event/venue creation, and live agent bus message log monitoring. |
| **Staff** | `staff@eventsphere.local` | `staff123` | QR code scanning and ticket validation portal (`/scan`). |
| **Attendee** | `demo@eventsphere.local` | `demo1234` | Event booking, private hire bidding with multi-turn bargaining, waitlist registration, and ticket viewing. |

*(1-Click login buttons for all three accounts are built directly into `/login`).*

---

## 4. Local Setup & Execution

### Prerequisites
- Python 3.10+ (tested on Python 3.11, 3.12, 3.13)
- Git

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/your-username/eventsphere.git
cd eventsphere

# 2. Create and activate a virtual environment
python -m venv .venv

# On Windows (PowerShell / Command Prompt):
.venv\Scripts\activate
# On macOS / Linux:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

### Running the Application

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Access the application in your browser at: **`http://127.0.0.1:8000`**

---

## 5. Environment Variables

Create a `.env` file in the project root (copied from `.env.example`):

```bash
cp .env.example .env
```

| Variable | Description | Default / Example Value |
| :--- | :--- | :--- |
| `DATABASE_URL` | Database connection string. Automatically falls back to local SQLite if omitted. | `sqlite:///./eventsphere.db` *(or `postgresql+psycopg2://user:pass@host/db`)* |
| `SECRET_KEY` | Secret salt used for session authentication tokens and cryptographic CSRF. | `change-me-to-a-secure-random-key` |
| `SESSION_COOKIE` | Cookie identifier for authenticated browser sessions. | `eventsphere_session` |

---

## 6. Testing & Quality Assurance

EventSphere features **20 automated Pytest tests** verifying concurrent safety, double check-in prevention, FIPA protocols, and agent autonomy:

### Run All Tests
```bash
pytest -v
```

### Run Specific Test Suites
```bash
# Test FIPA Contract Net Protocol and Multi-Turn Bargaining:
pytest tests/test_agents.py -k "contract_net or negotiation" -v

# Test Overbooking and Concurrency:
pytest tests/test_agents.py -k "overbooking" -v

# Test Double Check-in Prevention:
pytest tests/test_agents.py -k "double_check_in" -v
```

### Test Coverage Matrix (20 Tests)

| # | Test Name | Target Agent | Verification Scope |
| :--- | :--- | :--- | :--- |
| 1 | `test_booking_agent_prevents_overbooking` | **BookingAgent** | **REQUIRED**: Concurrent `asyncio.gather` requests for the last seat ensure exactly one `AGREE` and one `REFUSE`. |
| 2 | `test_ticket_agent_double_check_in_refused` | **TicketAgent** | **REQUIRED**: Same QR code scanned twice refuses second scan with `"already checked in"`. |
| 3 | `test_fipa_contract_net_protocol_orchestration` | **VenueAgent** | Standard FIPA CNP: CFP broadcast, participant proposals, accept/reject, and INFORM. |
| 4 | `test_fipa_contract_net_proposes_capable_alternative` | **VenueAgent** | Recommends capable alternative venue when requested venue exceeds guest capacity. |
| 5 | `test_iterated_negotiation_counter_offer_concession` | **VenueAgent** | Multi-turn bargaining: Initiator counter-CFP (Round 2) prompts venue concession. |
| 6 | `test_iterated_negotiation_redirects_to_cheaper_budget_alternative` | **VenueAgent** | When target venue floor exceeds user budget, agent falls back to cheaper candidate bid. |
| 7 | `test_venue_agent_prevents_hire_double_booking` | **VenueAgent** | Confirmed private hires block subsequent hire requests during the same slot. |
| 8 | `test_venue_agent_refuses_clash` | **VenueAgent** | Verifies 30-minute turnaround buffer between events and private hires. |
| 9 | `test_venue_agent_refuses_over_capacity` | **VenueAgent** | Prevents booking when ticket count exceeds venue physical capacity. |
| 10 | `test_venue_agent_prices_weekend_hire` | **VenueAgent** | Calculates 1.2x weekend surcharge for private hires on Saturday/Sunday. |
| 11 | `test_venue_agent_proposes_alternatives_on_clash` | **VenueAgent** | Suggests next available free turnaround slots when a calendar clash occurs. |
| 12 | `test_venue_agent_validates_dates_and_times` | **VenueAgent** | Rejects past start times and inverted end times. |
| 13 | `test_venue_agent_escalation_rules` | **VenueAgent** | Escalates short notice (<24h), large bookings (>80%), or long hires (>6h) for admin approval. |
| 14 | `test_booking_agent_books_and_issues_tickets` | **BookingAgent** | Creates booking records, updates stock, and triggers ticket generation. |
| 15 | `test_booking_agent_refuses_over_six` | **BookingAgent** | Enforces anti-scalping purchase limit of 6 tickets per booking. |
| 16 | `test_booking_agent_refund_policy_buckets` | **BookingAgent** | Verifies tiered cancellation refunds (100% for >7d, 50% for 1–7d, 0% for <24h). |
| 17 | `test_ticket_agent_issues_unique_codes` | **TicketAgent** | Generates non-colliding `ES-XXXXXX` unique ticket identifiers. |
| 18 | `test_waitlist_agent_expires_holds_and_reoffers` | **WaitlistAgent** | Proactive sweep expires stale reservation holds and offers tickets to next in line. |
| 19 | `test_notification_agent_records_broadcast` | **NotificationAgent** | Broadcast wildcard (`*`) notifications are properly stored for recipient users. |
| 20 | `test_full_pipeline_sells_out_and_refuses` | **Full Pipeline** | End-to-end integration: inventory depletion cleanly triggers `REFUSE`. |
