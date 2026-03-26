from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Enum, Text, Float, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import enum
import uuid
from datetime import datetime, timezone

from .database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class RoleEnum(enum.Enum):
    admin = "admin"
    owner = "owner"
    dispatcher = "dispatcher"
    technician = "technician"
    agent = "agent"

class MessageSenderEnum(enum.Enum):
    tenant = "tenant"
    ai = "ai"
    admin = "admin"

class MessageTypeEnum(enum.Enum):
    text = "text"
    image = "image"
    video = "video"
    audio = "audio"
    document = "document"
    mixed = "mixed"

class TicketStatusEnum(enum.Enum):
    new = "new"
    assigned = "assigned"
    scheduled = "scheduled"
    done = "done"
    cancelled = "cancelled"

class ConversationStatusEnum(enum.Enum):
    open = "open"
    closed = "closed"

class ConversationStateEnum(enum.Enum):
    new_conversation = "new_conversation"
    gathering = "gathering"
    classified_service = "classified_service"
    classified_faq = "classified_faq"
    classified_billing = "classified_billing"
    classified_announcement = "classified_announcement"
    service_collecting_details = "service_collecting_details"
    service_assessing_urgency = "service_assessing_urgency"
    service_scheduling = "service_scheduling"
    service_ready_for_ticket = "service_ready_for_ticket"
    ticket_created = "ticket_created"
    technician_assigned = "technician_assigned"
    managing_ticket = "managing_ticket"
    escalated_to_human = "escalated_to_human"
    closed = "closed"

class ScenarioEnum(enum.Enum):
    service = "service"
    faq = "faq"
    billing = "billing"
    announcement = "announcement"
    unknown = "unknown"

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, index=True)
    email = Column(String, unique=True, index=True)
    phone = Column(String, nullable=True, default="")
    password_hash = Column(String)
    role = Column(Enum(RoleEnum), default=RoleEnum.dispatcher)
    is_head = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)

    buildings = relationship("Building", back_populates="owner")
    assigned_tickets = relationship("Ticket", back_populates="assignee")
    schedules = relationship("TechnicianSchedule", back_populates="technician", cascade="all, delete-orphan")


class Building(Base):
    __tablename__ = "buildings"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    address = Column(String)
    house_number = Column(String, nullable=True)
    legal_number = Column(String, nullable=True)
    floor = Column(String, nullable=True)
    block = Column(String, nullable=True)
    actual_number = Column(String, nullable=True)
    owner_id = Column(String, ForeignKey("users.id"), index=True)

    owner = relationship("User", back_populates="buildings")
    tenants = relationship("Tenant", back_populates="building")


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    phone = Column(String, index=True)
    email = Column(String, nullable=True)
    building_id = Column(Integer, ForeignKey("buildings.id"), index=True)
    apartment = Column(String)
    lease_start_date = Column(String, nullable=True)       # дата начала аренды
    lease_end_date = Column(String, nullable=True)        # дата окончания аренды
    adults = Column(Integer, nullable=True)               # количество взрослых
    children = Column(Integer, nullable=True)             # количество детей
    has_pets = Column(Boolean, nullable=True)             # питомцы да/нет
    parking = Column(Boolean, nullable=True)              # parking yes/no
    parking_slot = Column(String, nullable=True)          # номер парковочного места
    emergency_contact = Column(String, nullable=True)     # контакт для экстренных случаев
    notes = Column(Text, nullable=True)                   # дополнительно (свободный текст)
    category = Column(String, nullable=True)               # категория клиента: A, B, C, no_service
    company = Column(String, nullable=True)               # компания клиента
    agent_enabled = Column(Boolean, nullable=False, default=False)  # AI agent support on/off

    building = relationship("Building", back_populates="tenants")
    conversations = relationship("Conversation", back_populates="tenant")
    tickets = relationship("Ticket", back_populates="tenant")


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), index=True)
    whatsapp_chat_id = Column(String, unique=True, index=True)
    status = Column(Enum(ConversationStatusEnum), default=ConversationStatusEnum.open)
    state = Column(Enum(ConversationStateEnum), default=ConversationStateEnum.new_conversation)
    scenario = Column(Enum(ScenarioEnum), nullable=True)
    classifier_confidence = Column(Float, nullable=True)
    context_data = Column(JSON, nullable=True, default=dict)
    created_at = Column(DateTime, default=_utcnow)
    reopened_at = Column(DateTime, nullable=True)
    escalated_at = Column(DateTime, nullable=True)

    tenant = relationship("Tenant", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), index=True)
    sender = Column(Enum(MessageSenderEnum))
    message_type = Column(Enum(MessageTypeEnum))
    content = Column(Text, nullable=True)
    media_url = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    conversation = relationship("Conversation", back_populates="messages")


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, index=True)
    ticket_number = Column(String, unique=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), index=True)
    category = Column(String)
    urgency = Column(String)
    description = Column(Text)
    photo_urls = Column(JSON, nullable=True, default=list)
    availability_time = Column(String)
    assigned_to = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    status = Column(Enum(TicketStatusEnum), default=TicketStatusEnum.new)
    scheduled_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    tenant = relationship("Tenant", back_populates="tickets")
    assignee = relationship("User", back_populates="assigned_tickets")
    notes = relationship("TicketNote", back_populates="ticket", cascade="all, delete-orphan")


class TicketNote(Base):
    __tablename__ = "ticket_notes"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), index=True)
    author_id = Column(String, ForeignKey("users.id"), index=True)
    text = Column(Text)
    created_at = Column(DateTime, default=_utcnow)

    ticket = relationship("Ticket", back_populates="notes")
    author = relationship("User")


class TechnicianSchedule(Base):
    __tablename__ = "technician_schedules"

    id = Column(Integer, primary_key=True, index=True)
    technician_id = Column(String, ForeignKey("users.id"), index=True)
    day_of_week = Column(Integer)   # 0=Monday .. 6=Sunday (ISO weekday)
    start_time = Column(String)     # "09:00" (HH:MM, local timezone)
    end_time = Column(String)       # "18:00"

    technician = relationship("User", back_populates="schedules")
