# Services — Business Logic & Integrations

## Files
```
services/
├── adapters.py      # Protocol implementations bridging ORM ↔ agent DTOs
├── scheduler.py     # Technician scheduling, slot search, ticket CRUD
├── buffer.py        # Message aggregation (5s window for rapid-fire WhatsApp messages)
├── notifier.py      # WhatsApp notifications via Green API (replies, escalation alerts)
├── orchestrator.py  # Thin shim delegating to AgentEngine (backward compat)
└── classifier.py    # Deprecated two-step LLM pipeline (replaced by agent/engine.py)
```

## Key Components

### adapters.py
Factory function `create_agent_engine(db)` wires everything together. Implements:
- `SqlConversationStore` — tenant lookup, conversation state, message history
- `SqlSchedulingService` — wraps scheduler.py functions + ticket operations
- `SqlNotificationService` — WhatsApp message sending

### scheduler.py
- `find_available_slots(db, category, urgency)` — search across urgency-based time window
- `find_slots_for_date(db, category, target_date)` — all slots for a specific date
- `find_slot_for_time(db, category, target_date, hour, minute)` — check exact time
- `create_ticket_from_context(db, tenant_id, context_data, conversation_id)` — auto-create ticket
- All technicians are universal (no specialty filtering)
- Timezone: UTC+5 (Almaty), 1-hour slot duration, 30-min intervals

### buffer.py
- `MessageBuffer` singleton (`message_buffer`) collects rapid-fire messages per chat
- Flushes after 5 seconds of silence
- Two modes: fire-and-forget (webhook) and blocking (test endpoint)

### notifier.py
- `send_whatsapp_reply()` — send AI reply to tenant via Green API
- `send_escalation_alert()` — notify dispatcher group chat on escalation
- `generate_escalation_message()` / `generate_tech_assignment_message()` — LLM-generated notifications
