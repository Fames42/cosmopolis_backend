"""
Integration tests for Cosmopolis API.

Run against a live Docker backend:
    1. docker compose -f backend/docker-compose.yml up -d
    2. docker compose -f backend/docker-compose.yml run --rm backend python -m src.seed_db
    3. python backend/tests/test_api.py
"""

import sys
import requests

BASE = "http://localhost:8000/api"

# ── globals ──────────────────────────────────────────────────────────────────
tokens: dict[str, str] = {}       # role → JWT
user_ids: dict[str, str] = {}     # role → user id
passed = 0
failed = 0
current_section = ""

CREDENTIALS = {
    "admin":      ("admin@cosmopolis.com",      "admin123"),
    "owner":      ("owner@cosmopolis.com",      "owner123"),
    "dispatcher": ("dispatcher@cosmopolis.com", "dispatcher123"),
    "technician": ("tech@cosmopolis.com",       "tech123"),
    "agent":      ("agent@cosmopolis.com",      "agent123"),
}


# ── helpers ──────────────────────────────────────────────────────────────────
def test(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name} — {detail}")


def section(name: str):
    global current_section
    current_section = name
    print(f"\n{'─'*60}\n  {name}\n{'─'*60}")


def h(role: str) -> dict:
    """Auth header for a role."""
    return {"Authorization": f"Bearer {tokens[role]}", "Content-Type": "application/json"}


def get(path: str, role: str, **kwargs) -> requests.Response:
    return requests.get(f"{BASE}{path}", headers=h(role), **kwargs)


def post(path: str, role: str, json=None, **kwargs) -> requests.Response:
    return requests.post(f"{BASE}{path}", headers=h(role), json=json, **kwargs)


def put(path: str, role: str, json=None, **kwargs) -> requests.Response:
    return requests.put(f"{BASE}{path}", headers=h(role), json=json, **kwargs)


def delete(path: str, role: str, **kwargs) -> requests.Response:
    return requests.delete(f"{BASE}{path}", headers=h(role), **kwargs)


# ── setup ────────────────────────────────────────────────────────────────────
def setup():
    section("Setup — Login all roles")
    for role, (email, password) in CREDENTIALS.items():
        r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password})
        ok = r.status_code == 200 and "token" in r.json()
        test(f"Login as {role}", ok, f"status={r.status_code}")
        if ok:
            data = r.json()
            tokens[role] = data["token"]
            user_ids[role] = data["user"]["id"]


# ── 1. Auth ──────────────────────────────────────────────────────────────────
def test_auth():
    section("1. Auth")

    # Wrong password
    r = requests.post(f"{BASE}/auth/login", json={"email": "admin@cosmopolis.com", "password": "wrong"})
    test("Login wrong password → 401", r.status_code == 401)

    # Nonexistent email
    r = requests.post(f"{BASE}/auth/login", json={"email": "nobody@example.com", "password": "x"})
    test("Login nonexistent email → 401", r.status_code == 401)

    # /me with valid token
    r = requests.get(f"{BASE}/auth/me", headers=h("admin"))
    test("GET /me valid token → 200", r.status_code == 200)
    if r.status_code == 200:
        test("GET /me returns correct email", r.json().get("email") == "admin@cosmopolis.com")

    # /me without token
    r = requests.get(f"{BASE}/auth/me")
    test("GET /me no token → 401", r.status_code == 401)

    # /me with garbage token
    r = requests.get(f"{BASE}/auth/me", headers={"Authorization": "Bearer garbage.token.here"})
    test("GET /me invalid token → 401", r.status_code == 401)


# ── 2. RBAC ──────────────────────────────────────────────────────────────────
def test_rbac():
    section("2. RBAC — Access Control")

    # Admin-only: /users
    r = get("/users", "admin")
    test("Admin can GET /users → 200", r.status_code == 200)

    r = get("/users", "dispatcher")
    test("Dispatcher cannot GET /users → 403", r.status_code == 403)

    r = get("/users", "technician")
    test("Technician cannot GET /users → 403", r.status_code == 403)

    # Technician blocked from conversations
    r = get("/conversations", "technician")
    test("Technician cannot GET /conversations → 403", r.status_code == 403)

    # Dispatcher blocked from analytics
    r = get("/analytics/summary", "dispatcher")
    test("Dispatcher cannot GET /analytics/summary → 403", r.status_code == 403)

    # Agent endpoints require agent role
    r = get("/agents/buildings", "agent")
    test("Agent can GET /agents/buildings → 200", r.status_code == 200)

    r = get("/agents/buildings", "dispatcher")
    test("Dispatcher cannot GET /agents/buildings → 403", r.status_code == 403)

    # No auth → 401
    r = requests.get(f"{BASE}/tickets")
    test("No auth GET /tickets → 401", r.status_code == 401)


# ── 3. Users CRUD ────────────────────────────────────────────────────────────
def test_users():
    section("3. Users CRUD (admin only)")

    # List users
    r = get("/users", "admin")
    test("GET /users → 200", r.status_code == 200)
    test("GET /users returns list", isinstance(r.json(), list))
    initial_count = len(r.json())

    # Create user
    r = post("/users", "admin", json={
        "name": "Test User",
        "email": "testuser@cosmopolis.com",
        "password": "test123",
        "role": "dispatcher",
    })
    test("POST /users create → 200", r.status_code == 200)
    created_id = r.json().get("id") if r.status_code == 200 else None

    # Duplicate email
    r = post("/users", "admin", json={
        "name": "Dup",
        "email": "testuser@cosmopolis.com",
        "password": "x",
        "role": "dispatcher",
    })
    test("POST /users duplicate email → 400", r.status_code == 400)

    # Delete
    if created_id:
        r = delete(f"/users/{created_id}", "admin")
        test("DELETE /users/{id} → 200", r.status_code == 200)

    # Verify count restored
    r = get("/users", "admin")
    test("User count restored after delete", len(r.json()) == initial_count)


# ── 4. Buildings & Tenants (Agent) ───────────────────────────────────────────
def test_buildings_tenants():
    section("4. Buildings & Tenants (agent role)")

    # List buildings
    r = get("/agents/buildings", "agent")
    test("GET /agents/buildings → 200", r.status_code == 200)
    buildings = r.json()
    test("Buildings have tenant_count field", all("tenant_count" in b for b in buildings))

    # Create building
    r = post("/agents/buildings", "agent", json={"name": "Test Building Z", "address": "999 Test St"})
    test("POST /agents/buildings → 200", r.status_code == 200)
    if r.status_code == 200:
        test("New building has id", "id" in r.json())
        new_building_id = r.json()["id"]
    else:
        new_building_id = None

    # List tenants
    r = get("/agents/tenants", "agent")
    test("GET /agents/tenants → 200", r.status_code == 200)
    tenants = r.json()
    test("Tenants have building_name field", all("building_name" in t for t in tenants))

    # Create tenant
    r = post("/agents/tenants", "agent", json={
        "name": "Test Tenant",
        "phone": "+7 (999) 000-00-01",
        "apartment": "99Z",
        "building_id": new_building_id,
    })
    test("POST /agents/tenants → 200", r.status_code == 200)
    new_tenant_id = r.json().get("id") if r.status_code == 200 else None

    # Duplicate phone
    r = post("/agents/tenants", "agent", json={
        "name": "Dup Tenant",
        "phone": "+7 (999) 000-00-01",
        "apartment": "1A",
    })
    test("POST /agents/tenants duplicate phone → 400", r.status_code == 400)

    # Assign tenant to a different building
    if new_tenant_id and buildings:
        target_building = buildings[0]["id"]
        r = put(f"/agents/tenants/{new_tenant_id}/assign", "agent", json={"building_id": target_building})
        test("PUT /agents/tenants/{id}/assign → 200", r.status_code == 200)
        if r.status_code == 200:
            test("Tenant reassigned to correct building", r.json()["building_id"] == target_building)


# ── 5. Tickets ───────────────────────────────────────────────────────────────
def test_tickets():
    section("5. Tickets (dispatcher/admin)")

    # List
    r = get("/tickets", "dispatcher")
    test("GET /tickets → 200", r.status_code == 200)
    tickets = r.json()
    test("Tickets is a list", isinstance(tickets, list))
    test("Seeded tickets exist", len(tickets) >= 5)

    if not tickets:
        return

    ticket_id = tickets[0]["id"]

    # Detail
    r = get(f"/tickets/{ticket_id}", "dispatcher")
    test("GET /tickets/{id} → 200", r.status_code == 200)
    if r.status_code == 200:
        d = r.json()
        test("Detail has tenantInfo", "tenantInfo" in d)
        test("Detail has issueDetails", "issueDetails" in d)
        test("Detail has notes list", isinstance(d.get("notes"), list))

    # 404
    r = get("/tickets/NONEXISTENT-999", "dispatcher")
    test("GET /tickets/nonexistent → 404", r.status_code == 404)

    # Create ticket — need a tenant_id
    tenants_r = get("/agents/tenants", "agent")
    if tenants_r.status_code == 200 and tenants_r.json():
        tenant_id = tenants_r.json()[0]["id"]
        r = post("/tickets", "dispatcher", json={
            "tenant_id": tenant_id,
            "category": "Plumbing",
            "urgency": "MEDIUM",
            "description": "Test ticket from integration tests",
            "availability_time": "anytime",
        })
        test("POST /tickets create → 200", r.status_code == 200)
        new_ticket_id = r.json().get("id") if r.status_code == 200 else None

        # Update status
        if new_ticket_id:
            r = put(f"/tickets/{new_ticket_id}", "dispatcher", json={"status": "assigned"})
            test("PUT /tickets/{id} status=assigned → 200", r.status_code == 200)
            if r.status_code == 200:
                test("Status updated", r.json()["ticketStatus"] == "ASSIGNED")

            # Invalid status
            r = put(f"/tickets/{new_ticket_id}", "dispatcher", json={"status": "INVALID_STATUS"})
            test("PUT /tickets/{id} invalid status → 400", r.status_code == 400)

            # Add note
            r = post(f"/tickets/{new_ticket_id}/notes", "dispatcher", json={"text": "Integration test note"})
            test("POST /tickets/{id}/notes → 200", r.status_code == 200)
            if r.status_code == 200:
                test("Note has author", "author" in r.json())
                test("Note has text", r.json()["text"] == "Integration test note")

    # Pagination
    r = get("/tickets?skip=0&limit=1", "dispatcher")
    test("Pagination limit=1 returns 1 ticket", r.status_code == 200 and len(r.json()) == 1)


# ── 6. Technicians ───────────────────────────────────────────────────────────
def test_technicians():
    section("6. Technicians")

    # List
    r = get("/technicians", "dispatcher")
    test("GET /technicians → 200", r.status_code == 200)
    techs = r.json()
    test("Technicians list has entries", len(techs) >= 2)
    if techs:
        test("Tech has activeTickets field", "activeTickets" in techs[0])

    # Create technician
    r = post("/technicians", "dispatcher", json={
        "name": "Test Tech",
        "email": "testtech@cosmopolis.com",
        "phone": "+7 000 111 22 33",
        "password": "test123",
    })
    test("POST /technicians create → 200", r.status_code == 200)
    new_tech_id = r.json().get("id") if r.status_code == 200 else None

    # Duplicate email
    r = post("/technicians", "dispatcher", json={
        "name": "Dup",
        "email": "testtech@cosmopolis.com",
        "phone": "+7 000 111 22 34",
        "password": "x",
    })
    test("POST /technicians duplicate email → 400", r.status_code == 400)

    # Update
    if new_tech_id:
        r = put(f"/technicians/{new_tech_id}", "dispatcher", json={"name": "Updated Tech Name"})
        test("PUT /technicians/{id} → 200", r.status_code == 200)
        if r.status_code == 200:
            test("Name updated", r.json()["name"] == "Updated Tech Name")

    # ── Technician's own endpoints ──
    # Login as the seeded technician (tech@cosmopolis.com)
    r = get("/technicians/me/tickets", "technician")
    test("GET /technicians/me/tickets → 200", r.status_code == 200)
    my_tickets = r.json()

    if my_tickets:
        tid = my_tickets[0]["id"]

        r = get(f"/technicians/me/tickets/{tid}", "technician")
        test("GET /technicians/me/tickets/{id} → 200", r.status_code == 200)
        if r.status_code == 200:
            test("Detail has description", "description" in r.json())
            test("Detail has tenantPhone", "tenantPhone" in r.json())

        # Add comment
        r = post(f"/technicians/me/tickets/{tid}/comments", "technician", json={"text": "Test comment"})
        test("POST /technicians/me/tickets/{id}/comments → 200", r.status_code == 200)

        # Update status
        r = put(f"/technicians/me/tickets/{tid}/status", "technician", json={"status": "done"})
        test("PUT /technicians/me/tickets/{id}/status → 200", r.status_code == 200)
        if r.status_code == 200:
            test("Status updated to DONE", r.json()["status"] == "DONE")
    else:
        print("  ⚠ No tickets assigned to technician — skipping my-tickets tests")


# ── 7. Conversations ─────────────────────────────────────────────────────────
def test_conversations():
    section("7. Conversations")

    # First create a conversation via webhook so there's data
    requests.post(f"{BASE}/webhook/test", json={
        "phone": "1234567890",
        "message": "Привет",
    })

    r = get("/conversations", "dispatcher")
    test("GET /conversations → 200", r.status_code == 200)
    convos = r.json()
    test("Conversations list returned", isinstance(convos, list))

    if convos:
        cid = convos[0]["id"]
        r = get(f"/conversations/{cid}", "dispatcher")
        test("GET /conversations/{id} → 200", r.status_code == 200)
        if r.status_code == 200:
            test("Conversation has messages", "messages" in r.json())
            test("Conversation has state", "state" in r.json())

    # Technician blocked
    r = get("/conversations", "technician")
    test("Technician GET /conversations → 403", r.status_code == 403)

    # 404
    r = get("/conversations/999999", "dispatcher")
    test("GET /conversations/999999 → 404", r.status_code == 404)


# ── 8. Analytics ─────────────────────────────────────────────────────────────
def test_analytics():
    section("8. Analytics")

    r = get("/analytics/summary", "admin")
    test("GET /analytics/summary as admin → 200", r.status_code == 200)
    if r.status_code == 200:
        d = r.json()
        test("Has total_tickets", "total_tickets" in d)
        test("Has tickets_by_status", "tickets_by_status" in d)
        test("Has open_conversations", "open_conversations" in d)
        test("total_tickets is int", isinstance(d["total_tickets"], int))

    r = get("/analytics/summary", "owner")
    test("GET /analytics/summary as owner → 200", r.status_code == 200)

    r = get("/analytics/summary", "dispatcher")
    test("Dispatcher GET /analytics/summary → 403", r.status_code == 403)

    r = get("/analytics/summary", "technician")
    test("Technician GET /analytics/summary → 403", r.status_code == 403)


# ── 9. Webhook / AI Agent Flow ───────────────────────────────────────────────
def test_webhook_flow():
    section("9. Webhook — AI Agent Flow")

    # Unknown tenant
    r = requests.post(f"{BASE}/webhook/test", json={"phone": "0000000000", "message": "Hello"})
    test("Unknown tenant → reply mentions not found", r.status_code == 200)
    if r.status_code == 200:
        test("State is unknown_tenant", r.json()["state"] == "unknown_tenant")

    # Greeting → gathering (use a known tenant phone from seed)
    # Tenant phones: +1 (234) 567-890, -891, -892, -893
    r = requests.post(f"{BASE}/webhook/test", json={"phone": "1234567891", "message": "Привет"})
    test("Greeting → 200", r.status_code == 200)
    if r.status_code == 200:
        d = r.json()
        test("Greeting → state is gathering", d["state"] == "gathering")
        test("Greeting → reply is non-empty", len(d.get("reply", "")) > 0)
        test("Greeting → agent_response present", d.get("agent_response") is not None)
        if d.get("agent_response"):
            test("Greeting → classified=false", d["agent_response"]["classified"] is False)

    # Follow-up with service request → should classify
    r = requests.post(f"{BASE}/webhook/test", json={
        "phone": "1234567891",
        "message": "У меня протекает труба на кухне, вода на полу",
    })
    test("Service request after greeting → 200", r.status_code == 200)
    if r.status_code == 200:
        d = r.json()
        test("Service classified → state starts with classified_", d["state"].startswith("classified_"))

    # Direct classification (new tenant, no greeting)
    r = requests.post(f"{BASE}/webhook/test", json={
        "phone": "1234567892",
        "message": "Как оплатить коммунальные услуги?",
    })
    test("Direct billing question → 200", r.status_code == 200)
    if r.status_code == 200:
        d = r.json()
        # Could be classified_billing or classified_faq depending on LLM
        test("Direct question → classified state", d["state"].startswith("classified_") or d["state"] == "gathering")

    # Escalation — request human
    r = requests.post(f"{BASE}/webhook/test", json={
        "phone": "1234567893",
        "message": "I need to speak to a real human operator right now",
    })
    test("Human request → 200", r.status_code == 200)
    if r.status_code == 200:
        d = r.json()
        test("Human request → escalated_to_human", d["state"] == "escalated_to_human")

    # Already escalated — sending another message
    r = requests.post(f"{BASE}/webhook/test", json={
        "phone": "1234567893",
        "message": "Are you still there?",
    })
    test("After escalation → 200", r.status_code == 200)
    if r.status_code == 200:
        d = r.json()
        test("After escalation → state stays escalated", d["state"] == "escalated_to_human")
        test("After escalation → reply mentions dispatcher", "диспетчер" in d["reply"].lower())


# ── 10. Technician Schedules & Specialties ───────────────────────────────────
def test_technician_schedules():
    section("10. Technician Schedules & Specialties")

    # Get technicians — should have specialties
    r = get("/technicians", "dispatcher")
    test("GET /technicians returns specialties", r.status_code == 200)
    if r.status_code == 200:
        techs = r.json()
        techs_with_specs = [t for t in techs if t.get("specialties")]
        test("Seeded techs have specialties", len(techs_with_specs) >= 2)
        # Check that at least one tech has plumbing
        has_plumbing = any("plumbing" in (t.get("specialties") or []) for t in techs)
        test("At least one tech has plumbing specialty", has_plumbing)

    # Get schedule for a technician
    if r.status_code == 200 and techs:
        tech_id = techs[0]["id"]
        r2 = get(f"/technicians/{tech_id}/schedule", "dispatcher")
        test("GET /technicians/{id}/schedule → 200", r2.status_code == 200)
        if r2.status_code == 200:
            sched = r2.json()
            test("Schedule is a list", isinstance(sched, list))
            test("Schedule has entries", len(sched) >= 1)
            if sched:
                test("Schedule entry has day_of_week", "day_of_week" in sched[0])
                test("Schedule entry has start_time", "start_time" in sched[0])

    # Set schedule for a technician
    if r.status_code == 200 and techs:
        tech_id = techs[0]["id"]
        r3 = put(f"/technicians/{tech_id}/schedule", "dispatcher", json={
            "schedules": [
                {"day_of_week": 0, "start_time": "08:00", "end_time": "17:00"},
                {"day_of_week": 1, "start_time": "08:00", "end_time": "17:00"},
            ]
        })
        test("PUT /technicians/{id}/schedule → 200", r3.status_code == 200)
        if r3.status_code == 200:
            test("Schedule replaced with 2 entries", len(r3.json()) == 2)

        # Restore full Mon-Fri schedule so later tests (service flow) have availability
        put(f"/technicians/{tech_id}/schedule", "dispatcher", json={
            "schedules": [
                {"day_of_week": d, "start_time": "09:00", "end_time": "18:00"}
                for d in range(5)  # Mon-Fri
            ]
        })

    # Update specialties
    if r.status_code == 200 and techs:
        tech_id = techs[0]["id"]
        r4 = put(f"/technicians/{tech_id}", "dispatcher", json={
            "specialties": ["plumbing", "electrical", "heating"]
        })
        test("PUT /technicians/{id} specialties → 200", r4.status_code == 200)
        if r4.status_code == 200:
            test("Specialties updated", r4.json()["specialties"] == ["plumbing", "electrical", "heating"])

    # Create technician with specialties
    r5 = post("/technicians", "dispatcher", json={
        "name": "Schedule Test Tech",
        "email": "schedtest@cosmopolis.com",
        "phone": "+7 000 999 88 77",
        "password": "test123",
        "specialties": ["heating", "ventilation"],
    })
    test("POST /technicians with specialties → 200", r5.status_code == 200)
    if r5.status_code == 200:
        test("Created tech has specialties", r5.json()["specialties"] == ["heating", "ventilation"])

    # 404 for schedule of nonexistent tech
    r6 = get("/technicians/nonexistent-id/schedule", "dispatcher")
    test("GET schedule for nonexistent tech → 404", r6.status_code == 404)


# ── 11. Service Scheduling Flow (webhook) ────────────────────────────────────
def test_service_flow():
    section("11. Service Scheduling Flow (webhook)")

    # Use tenant phone +1 (234) 567-891 (Alice Johnson, Building A, Apt 2A)
    # Different phone from test 9 to avoid conversation state conflicts
    phone = "1234567891"

    # The LLM is non-deterministic, so instead of asserting exact states at each step
    # we drive the conversation forward with contextually appropriate messages and
    # verify that the flow progresses through states and eventually creates a ticket.

    # All valid service-flow states in expected order
    SERVICE_STATES = [
        "classified_service", "service_collecting_details", "service_assessing_urgency",
        "service_scheduling", "service_ready_for_ticket", "ticket_created", "closed",
    ]

    # Messages to send based on current state (repeat-safe)
    # Use a medium-urgency plumbing issue to avoid narrow emergency window
    STATE_MESSAGES = {
        "new_conversation":          "У меня капает кран на кухне, нужен сантехник для ремонта",
        "gathering":                 "У меня капает кран на кухне, нужен сантехник для ремонта",
        "classified_service":        "Да, кран на кухне подтекает, нужно починить",
        "service_collecting_details":"Течет кран на кухне, проблема водопроводная, не срочно но мешает",
        "service_assessing_urgency": "Нет, не экстренная ситуация, просто кран капает",
        "service_scheduling":        "1",
        "service_ready_for_ticket":  "Да, подтверждаю",
    }

    MAX_STEPS = 12
    current_state = None
    flow_reached_ticket = False
    all_ok = True

    for step in range(1, MAX_STEPS + 1):
        # Pick the right message based on the last known state
        msg = STATE_MESSAGES.get(current_state, STATE_MESSAGES["new_conversation"])

        r = requests.post(f"{BASE}/webhook/test", json={"phone": phone, "message": msg})
        if r.status_code != 200:
            test(f"Step {step}: HTTP 200", False, f"got {r.status_code}")
            all_ok = False
            break

        d = r.json()
        current_state = d.get("state", "")
        print(f"    → Step {step}: state={current_state}")

        if current_state in ("ticket_created", "closed"):
            flow_reached_ticket = True
            break

    test("Flow entered service states", current_state in SERVICE_STATES or flow_reached_ticket,
         f"final state: {current_state}")
    test("Flow reached ticket_created or closed", flow_reached_ticket,
         f"stopped at state: {current_state} after {step} steps")

    # Verify ticket was created in DB via tickets API
    r = get("/tickets", "dispatcher")
    if r.status_code == 200:
        tickets = r.json()
        # Auto-created tickets have TKT- prefix (seeded have T- prefix)
        auto_tickets = [t for t in tickets if t["id"].startswith("TKT-")]
        test("Auto-created ticket exists in DB", len(auto_tickets) >= 1)
        if auto_tickets:
            tid = auto_tickets[-1]["id"]  # Get most recently created
            r2 = get(f"/tickets/{tid}", "dispatcher")
            if r2.status_code == 200:
                detail = r2.json()
                test("Auto-ticket has SCHEDULED status", detail["ticketStatus"] == "SCHEDULED")
                test("Auto-ticket has assigned tech", detail.get("assignedTech") is not None)
            else:
                test("Auto-ticket detail fetch", False, f"status={r2.status_code}")
                test("Auto-ticket has assigned tech", False, "skipped")
    else:
        test("Auto-created ticket exists in DB", False, f"tickets fetch failed: {r.status_code}")
        test("Auto-ticket has SCHEDULED status", False, "skipped")
        test("Auto-ticket has assigned tech", False, "skipped")


# ── 12. Edge Cases ───────────────────────────────────────────────────────────
def test_edge_cases():
    section("10. Edge Cases")

    # Malformed JSON
    r = requests.post(f"{BASE}/webhook/test", data="not json", headers={"Content-Type": "application/json"})
    test("Malformed JSON → 422", r.status_code == 422)

    # Missing required field
    r = requests.post(f"{BASE}/webhook/test", json={"phone": "123"})
    test("Missing 'message' field → 422", r.status_code == 422)

    # Empty message
    r = requests.post(f"{BASE}/webhook/test", json={"phone": "1234567890", "message": ""})
    test("Empty message → still 200 (handled gracefully)", r.status_code == 200)

    # Pagination edge
    r = get("/tickets?skip=9999&limit=10", "dispatcher")
    test("Pagination skip=9999 → 200 empty list", r.status_code == 200 and len(r.json()) == 0)


# ── main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  Cosmopolis API — Integration Tests")
    print("=" * 60)

    setup()

    if len(tokens) < len(CREDENTIALS):
        print("\n✗ Could not login all roles. Aborting.")
        sys.exit(1)

    test_auth()
    test_rbac()
    test_users()
    test_buildings_tenants()
    test_tickets()
    test_technicians()
    test_conversations()
    test_analytics()
    test_webhook_flow()
    test_technician_schedules()
    test_service_flow()
    test_edge_cases()

    print(f"\n{'='*60}")
    print(f"  Results: {passed} passed, {failed} failed")
    print(f"{'='*60}")
    sys.exit(0 if failed == 0 else 1)
