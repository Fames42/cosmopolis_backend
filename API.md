# Cosmopolis API Documentation

Base URL: `http://localhost:8000/api`

Auto-generated interactive docs: [`/docs`](http://localhost:8000/docs) (Swagger) | [`/redoc`](http://localhost:8000/redoc)

---

## Authentication

All endpoints (except Webhook) require a Bearer token in the `Authorization` header:

```
Authorization: Bearer <token>
```

### Roles & Permissions

| Role         | Description                        |
|--------------|------------------------------------|
| `admin`      | Full access to all endpoints       |
| `owner`      | Building owner — analytics access  |
| `dispatcher` | Manages tickets & technicians      |
| `technician` | Views own tickets, updates status  |
| `agent`      | Manages buildings & tenants        |

---

## Auth (`/api/auth`)

### `POST /api/auth/token`

OAuth2 password flow. Used by Swagger UI.

| Field      | Type   | Description        |
|------------|--------|--------------------|
| `username` | string | Email address      |
| `password` | string | Account password   |

**Response:** `{ access_token, token_type, user: { id, name, email, role } }`

---

### `POST /api/auth/login`

Standard login endpoint for the frontend.

**Request body:**
```json
{
  "email": "admin@cosmorent.kz",
  "password": "admin123"
}
```

**Response:**
```json
{
  "token": "eyJ...",
  "user": { "id": "uuid", "email": "admin@cosmorent.kz", "role": "ADMIN" }
}
```

---

### `GET /api/auth/me`

**Auth:** Any authenticated user

**Response:** `{ id, name, email, role, created_at }`

---

## Users (`/api/users`) — Admin only

### `POST /api/users`

Create a new user.

**Request body:**
```json
{
  "name": "John Doe",
  "email": "john@example.com",
  "password": "secret",
  "role": "dispatcher",
  "phone": "+77771234567",
  "is_head": false
}
```

`phone` and `is_head` are optional. `is_head` grants a technician dispatcher-level access.

**Response:** `UserResponse { id, name, email, role, phone, is_head, created_at }`

---

### `GET /api/users`

List all users.

| Param   | Type | Default | Description       |
|---------|------|---------|-------------------|
| `skip`  | int  | 0       | Offset            |
| `limit` | int  | 100     | Max results       |

**Response:** `UserResponse[]`

---

### `DELETE /api/users/{user_id}`

Delete a user by UUID.

**Response:** `UserResponse` (the deleted user)

---

## Tickets (`/api/tickets`)

### `GET /api/tickets`

List all tickets (dispatcher view).

**Auth:** Any authenticated user

| Param   | Type | Default | Description       |
|---------|------|---------|-------------------|
| `skip`  | int  | 0       | Offset            |
| `limit` | int  | 100     | Max results       |

**Response:**
```json
[
  {
    "id": "TKT-A1B2C3D4",
    "category": "plumbing",
    "urgency": "HIGH",
    "tenant": "Apt 12 (Building A)",
    "assignedTo": "Maksim",
    "status": "SCHEDULED",
    "scheduled": "2026-03-15T10:00:00",
    "created": "2026-03-14"
  }
]
```

---

### `GET /api/tickets/{ticket_id}`

Get ticket details. Accepts ticket number (`TKT-...`) or integer ID.

**Auth:** Any authenticated user

**Response:**
```json
{
  "id": "TKT-A1B2C3D4",
  "ticketStatus": "SCHEDULED",
  "assignedTech": "Maksim",
  "scheduledDate": "2026-03-15T10:00:00",
  "created": "2026-03-14",
  "tenantInfo": {
    "name": "Aisha",
    "phone": "+77761234567",
    "address": "123 Main St, Building A",
    "apartment": "Apt 12"
  },
  "issueDetails": {
    "category": "plumbing",
    "urgency": "HIGH",
    "description": "Leaking pipe under kitchen sink",
    "photo_urls": ["data:image/jpeg;base64,..."]
  },
  "notes": [
    { "id": 1, "author": "Dispatcher", "time": "2026-03-14T12:00:00", "text": "Urgent", "role": "dispatcher" }
  ]
}
```

---

### `POST /api/tickets`

Create a new ticket.

**Auth:** Admin or Dispatcher

**Request body:**
```json
{
  "tenant_id": 1,
  "category": "electrical",
  "urgency": "medium",
  "description": "Outlet not working in bedroom",
  "photo_urls": [],
  "availability_time": "morning",
  "status": "new",
  "scheduled_time": null
}
```

**Response:** `TicketDispatcherDetailResponse`

---

### `PUT /api/tickets/{ticket_id}`

Update a ticket (status, urgency, assignment, schedule).

**Auth:** Any authenticated user

**Request body** (all fields optional):
```json
{
  "status": "assigned",
  "urgency": "high",
  "assignedTo": "technician-uuid-or-name",
  "scheduledDate": "2026-03-16T14:00:00Z"
}
```

Valid urgency values: `low`, `medium`, `high`, `emergency` (case-insensitive)

All string fields (`status`, `urgency`) are case-insensitive on input.

**Response:** `TicketDispatcherDetailResponse`

---

### `POST /api/tickets/{ticket_id}/notes`

Add a note to a ticket.

**Auth:** Any authenticated user

**Request body:**
```json
{ "text": "Parts ordered, ETA tomorrow" }
```

**Response:** `{ id, author, time, text, role }`

---

### `GET /api/tickets/{ticket_id}/photo`

Get photos attached to a ticket.

**Auth:** Any authenticated user

**Response:** `{ photo_urls: ["data:image/jpeg;base64,..."] }`

**Errors:** `404` if no photos attached

---

### `POST /api/tickets/export`

Export selected tickets to an Excel (`.xlsx`) file.

**Auth:** Any authenticated user

**Request body:**
```json
{ "ticket_ids": ["TKT-A1B2C3D4", "TKT-E5F6G7H8"] }
```

**Response:** Binary file download (`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`)

---

## Technicians (`/api/technicians`)

### `GET /api/technicians`

List all technicians with active ticket counts.

**Auth:** Any authenticated user

**Response:**
```json
[
  {
    "id": "uuid",
    "name": "Maksim",
    "email": "maksim@cosmorent.kz",
    "phone": "+77771234567",
    "is_head": false,
    "activeTickets": 3,
    "status": "ACTIVE"
  }
]
```

---

### `POST /api/technicians`

Create a new technician.

**Auth:** Any authenticated user

**Request body:**
```json
{
  "name": "New Tech",
  "email": "tech@example.com",
  "phone": "+77770001122",
  "password": "pass123",
  "is_head": false
}
```

**Response:** `TechnicianResponse`

---

### `PUT /api/technicians/{tech_id}`

Update technician profile.

**Auth:** Any authenticated user

**Request body** (all fields optional):
```json
{
  "name": "Updated Name",
  "email": "new@example.com",
  "phone": "+77770009999",
  "is_head": true
}
```

**Response:** `TechnicianResponse`

---

### `GET /api/technicians/schedules`

Get all technicians' weekly schedules at once.

**Auth:** Dispatcher or Admin

**Response:**
```json
[
  {
    "technician_id": "uuid",
    "technician_name": "Maksim",
    "schedules": [
      { "day_of_week": 0, "start_time": "09:00", "end_time": "18:00" },
      { "day_of_week": 1, "start_time": "09:00", "end_time": "18:00" }
    ]
  }
]
```

`day_of_week`: 0 = Monday, 6 = Sunday (ISO weekday)

---

### `GET /api/technicians/{tech_id}/schedule`

Get a single technician's weekly schedule.

**Auth:** Dispatcher or Admin

**Response:**
```json
[
  { "id": 1, "technician_id": "uuid", "day_of_week": 0, "start_time": "09:00", "end_time": "18:00" },
  { "id": 2, "technician_id": "uuid", "day_of_week": 1, "start_time": "09:00", "end_time": "18:00" }
]
```

---

### `PUT /api/technicians/{tech_id}/schedule`

Bulk-replace a technician's weekly schedule. Deletes all existing entries and inserts the new ones.

**Auth:** Dispatcher or Admin

**Request body:**
```json
{
  "schedules": [
    { "day_of_week": 0, "start_time": "09:00", "end_time": "18:00" },
    { "day_of_week": 1, "start_time": "09:00", "end_time": "18:00" },
    { "day_of_week": 2, "start_time": "10:00", "end_time": "16:00" },
    { "day_of_week": 3, "start_time": "09:00", "end_time": "18:00" },
    { "day_of_week": 4, "start_time": "09:00", "end_time": "17:00" }
  ]
}
```

**Response:** `TechnicianScheduleResponse[]`

---

### `GET /api/technicians/{tech_id}/workload`

View a technician's assigned tickets, optionally filtered by scheduled date range.

**Auth:** Dispatcher or Admin

| Param       | Type       | Description                  |
|-------------|------------|------------------------------|
| `date_from` | date       | Start date (`YYYY-MM-DD`)    |
| `date_to`   | date       | End date (`YYYY-MM-DD`)      |

**Example:** `GET /api/technicians/{id}/workload?date_from=2026-03-15&date_to=2026-03-21`

**Response:**
```json
{
  "technician_id": "uuid",
  "technician_name": "Maksim",
  "tickets": [
    {
      "ticket_number": "TKT-A1B2C3D4",
      "category": "plumbing",
      "urgency": "high",
      "status": "scheduled",
      "scheduled_time": "2026-03-16T10:00:00",
      "description": "Leaking pipe under kitchen sink"
    }
  ]
}
```

---

### `GET /api/technicians/me/schedule`

Get the current technician's own weekly schedule.

**Auth:** Any authenticated user (intended for technicians)

**Response:**
```json
[
  { "id": 1, "technician_id": "uuid", "day_of_week": 0, "start_time": "09:00", "end_time": "18:00" },
  { "id": 2, "technician_id": "uuid", "day_of_week": 1, "start_time": "09:00", "end_time": "18:00" }
]
```

---

### `PUT /api/technicians/me/schedule`

Bulk-replace the current technician's own weekly schedule.

**Auth:** Any authenticated user (intended for technicians)

**Request body:**
```json
{
  "schedules": [
    { "day_of_week": 0, "start_time": "09:00", "end_time": "18:00" },
    { "day_of_week": 1, "start_time": "10:00", "end_time": "16:00" }
  ]
}
```

**Response:** `TechnicianScheduleResponse[]`

---

### `GET /api/technicians/me/tickets`

Get tickets assigned to the current user.

**Auth:** Any authenticated user (intended for technicians)

**Response:**
```json
[
  {
    "id": "TKT-A1B2C3D4",
    "category": "plumbing",
    "address": "Apt 12, Building A",
    "urgency": "HIGH",
    "scheduled": "Mar 16, 10:00",
    "status": "SCHEDULED",
    "isToday": false
  }
]
```

---

### `GET /api/technicians/me/tickets/{ticket_id}`

Get details of a ticket assigned to the current user.

**Auth:** Any authenticated user

**Response:**
```json
{
  "id": "TKT-A1B2C3D4",
  "category": "plumbing",
  "urgency": "HIGH",
  "address": "Apt 12, Building A",
  "description": "Leaking pipe under kitchen sink",
  "tenantPhone": "+77761234567",
  "status": "SCHEDULED",
  "comments": [{ "id": 1, "text": "On my way" }]
}
```

---

### `POST /api/technicians/me/tickets/{ticket_id}/comments`

Add a comment to a ticket assigned to the current user.

**Auth:** Any authenticated user

**Request body:**
```json
{ "text": "Parts replaced, testing now" }
```

**Response:** `{ id, text }`

---

### `PUT /api/technicians/me/tickets/{ticket_id}/status`

Update the status of a ticket assigned to the current user.

**Auth:** Any authenticated user

**Request body:**
```json
{ "status": "done" }
```

Valid statuses: `new`, `assigned`, `scheduled`, `done`, `cancelled`

**Response:** `TicketTechnicianDetailResponse`

---

## Conversations (`/api/conversations`)

### `GET /api/conversations`

List all WhatsApp conversations.

**Auth:** Any authenticated user **except** technician

| Param   | Type | Default | Description       |
|---------|------|---------|-------------------|
| `skip`  | int  | 0       | Offset            |
| `limit` | int  | 100     | Max results       |

**Response:**
```json
[
  {
    "id": 1,
    "tenant_id": 1,
    "whatsapp_chat_id": "77761234567@c.us",
    "status": "open",
    "state": "service_scheduling",
    "scenario": "service",
    "classifier_confidence": 0.92,
    "created_at": "2026-03-14T08:00:00",
    "messages": [
      { "id": 1, "conversation_id": 1, "sender": "tenant", "message_type": "text", "content": "Hello", "media_url": null, "created_at": "..." }
    ]
  }
]
```

---

### `GET /api/conversations/{conversation_id}`

Get a single conversation with all messages.

**Auth:** Any authenticated user except technician

**Response:** `ConversationResponse`

---

### `GET /api/conversations/{conversation_id}/messages/{message_id}/media`

Get media (image) attached to a specific message.

**Auth:** Any authenticated user except technician

**Response:** `{ media_url: "data:image/jpeg;base64,...", message_type: "image" }`

---

## Agents (`/api/agents`) — Admin or Agent only

### `GET /api/agents/buildings`

List all buildings with tenant counts.

| Param   | Type | Default |
|---------|------|---------|
| `skip`  | int  | 0       |
| `limit` | int  | 100     |

**Response:**
```json
[{
  "id": 1,
  "name": "ESENTAI APARTMENTS A",
  "address": "проспект Аль-Фараби",
  "house_number": "77/1",
  "legal_number": "42",
  "floor": "8",
  "block": "А",
  "actual_number": "8С",
  "tenant_count": 1
}]
```

Each building represents a specific unit/object (one per tenant row in the import).

---

### `POST /api/agents/buildings`

Create a new building (owner set to current user).

**Request body:**
```json
{
  "name": "Building D",
  "address": "456 Oak Ave",
  "house_number": "10",
  "legal_number": "42",
  "floor": "3",
  "block": "A",
  "actual_number": "5C"
}
```

All fields except `name` and `address` are optional.

**Response:** `BuildingListItem`

---

### `GET /api/agents/buildings/names`

List distinct building names with total tenant counts. Useful for populating dropdowns (e.g. broadcast notification target).

**Auth:** Admin or Agent

**Response:**
```json
[
  { "name": "ESENTAI APARTMENTS A", "tenant_count": 12 },
  { "name": "ESENTAI APARTMENTS B", "tenant_count": 8 }
]
```

---

### `GET /api/agents/buildings/filters`

Return available blocks and house numbers for a given building name. Useful for populating filter dropdowns after selecting a building.

**Auth:** Admin or Agent

| Param           | Type   | Required | Description            |
|-----------------|--------|----------|------------------------|
| `building_name` | string | Yes      | Building name to query |

**Example:** `GET /api/agents/buildings/filters?building_name=ESENTAI APARTMENTS A`

**Response:**
```json
{
  "blocks": ["А", "Б"],
  "house_numbers": ["77/1", "77/2"]
}
```

**Errors:** `404` if no buildings found with this name.

---

### `GET /api/agents/tenants`

List all tenants with building info.

| Param   | Type | Default |
|---------|------|---------|
| `skip`  | int  | 0       |
| `limit` | int  | 100     |

**Response:**
```json
[{
  "id": 1,
  "name": "Aisha",
  "phone": "+77761234567",
  "apartment": "12",
  "building_id": 1,
  "building_name": "Building A",
  "email": "aisha@example.com",
  "lease_start_date": "2025-09-01",
  "lease_end_date": "2026-09-01",
  "adults": 2,
  "children": 1,
  "has_pets": false,
  "parking": true,
  "parking_slot": "A-12",
  "emergency_contact": "+77001234567",
  "notes": "Предпочитает визиты после 14:00",
  "agent_enabled": true
}]
```

---

### `POST /api/agents/tenants`

Create a new tenant.

**Request body:**
```json
{
  "name": "New Tenant",
  "phone": "+77769999999",
  "apartment": "5A",
  "building_id": 1,
  "email": "tenant@example.com",
  "lease_start_date": "2026-01-15",
  "lease_end_date": "2027-01-15",
  "adults": 1,
  "children": 0,
  "has_pets": false,
  "parking": true,
  "parking_slot": "B-3",
  "emergency_contact": "+77009876543",
  "notes": null,
  "agent_enabled": true
}
```

All fields except `name`, `phone`, `apartment`, `building_id` are optional. `agent_enabled` defaults to `true`.

**Response:** `TenantListItem`

---

### `PUT /api/agents/tenants/{tenant_id}`

Update a tenant's information. All fields are optional.

**Request body:**
```json
{
  "email": "new@example.com",
  "adults": 2,
  "children": 1,
  "has_pets": true,
  "parking": false,
  "parking_slot": null,
  "emergency_contact": "+77001112233",
  "notes": "Свободный текст с пожеланиями"
}
```

**Response:** `TenantListItem`

---

### `DELETE /api/agents/tenants/{tenant_id}`

Delete a tenant.

**Response:** `{ "detail": "Tenant deleted" }`

---

### `PATCH /api/agents/tenants/{tenant_id}/agent-support`

Enable or disable AI agent support for a tenant. When disabled, the AI agent will not process messages from this tenant — they will receive a message to contact management directly.

**Request body:**
```json
{ "enabled": false }
```

**Response:** `TenantListItem`

---

### `PUT /api/agents/tenants/{tenant_id}/assign`

Assign a tenant to a building.

**Request body:**
```json
{ "building_id": 2 }
```

**Response:** `TenantListItem`

---

### `POST /api/agents/notifications/broadcast`

Send a WhatsApp notification to all tenants in a building, optionally filtered by block and house number.

**Auth:** Admin or Agent

**Request body:**
```json
{
  "building_name": "ESENTAI APARTMENTS A",
  "block": "А",
  "house_number": "77/1",
  "message": "Уважаемые жильцы, завтра будет отключена вода с 10:00 до 14:00."
}
```

| Field           | Type   | Required | Description                                    |
|-----------------|--------|----------|------------------------------------------------|
| `building_name` | string | Yes      | Building name (case-insensitive match)         |
| `block`         | string | No       | Filter by block (exact match)                  |
| `house_number`  | string | No       | Filter by house number (exact match)           |
| `message`       | string | Yes      | Notification text to send                      |

**Response:**
```json
{
  "total_tenants": 15,
  "sent": 14,
  "skipped": 1,
  "details": [
    { "tenant": "Иванов И.И.", "status": "sent" },
    { "tenant": "Петров П.П.", "status": "skipped", "reason": "no phone" }
  ]
}
```

**Errors:** `404` if no buildings match or no tenants found in matching buildings.

---

### Building Optional Fields Reference

| Field            | Type   | Description                          |
|------------------|--------|--------------------------------------|
| `house_number`   | string | № дома                               |
| `legal_number`   | string | Юридический номер квартиры (№ юр)    |
| `floor`          | string | Этаж                                 |
| `block`          | string | Блок                                 |
| `actual_number`  | string | Фактический номер квартиры (№ факт)  |

---

### Tenant Optional Fields Reference

| Field               | Type    | Description                                    |
|---------------------|---------|------------------------------------------------|
| `email`             | string  | Tenant email                                   |
| `lease_start_date`  | string  | Дата заселения (`YYYY-MM-DD`)                  |
| `lease_end_date`    | string  | Дата окончания аренды (`YYYY-MM-DD`)           |
| `adults`            | int     | Количество взрослых                            |
| `children`          | int     | Количество детей                               |
| `has_pets`          | bool    | Питомцы (да/нет)                               |
| `parking`           | bool    | Парковка (да/нет)                              |
| `parking_slot`      | string  | Номер парковочного места                       |
| `emergency_contact` | string  | Контакт для экстренных случаев                 |
| `notes`             | string  | Дополнительно (свободный текст)                |
| `agent_enabled`     | bool    | AI agent support on/off (default: `true`)      |

---

## Analytics (`/api/analytics`) — Admin or Owner only

### `GET /api/analytics/summary`

Dashboard statistics.

**Response:**
```json
{
  "total_tickets": 42,
  "tickets_by_status": {
    "new": 5,
    "assigned": 8,
    "scheduled": 12,
    "done": 15,
    "cancelled": 2
  },
  "open_conversations": 3
}
```

---

## Webhook (`/api/webhook`) — No auth required

### `POST /api/webhook/test`

Simulate an incoming WhatsApp message for testing the AI agent.

**Request body:**
```json
{ "phone": "77761234567", "message": "У меня течёт труба на кухне" }
```

**Response:**
```json
{
  "reply": "Здравствуйте! Я зафиксировал вашу проблему...",
  "state": "service_collecting_details",
  "agent_response": {
    "reply": "...",
    "classified": true,
    "scenario": "service",
    "confidence": 0.92,
    "subtype": null,
    "requires_human": false
  }
}
```

---

### `POST /api/webhook/greenapi`

Production webhook for incoming WhatsApp messages via Green API. Called automatically by the Green API service.

**Request body:** Raw Green API webhook payload (see Green API docs)

**Handled message types:** `textMessage`, `extendedTextMessage`, `imageMessage`

**Response:** `{ status: "ok" | "ignored", ... }`

---

## Error Responses

| Status | Description                                     |
|--------|-------------------------------------------------|
| `400`  | Bad request (validation error, duplicate email)  |
| `401`  | Missing or invalid authentication token          |
| `403`  | Insufficient role permissions                    |
| `404`  | Resource not found                               |

Error body format:
```json
{ "detail": "Error message here" }
```
