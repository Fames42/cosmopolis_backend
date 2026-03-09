from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Enum, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import enum
import uuid
from datetime import datetime, timezone

from .database import Base

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

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, index=True)
    email = Column(String, unique=True, index=True)
    phone = Column(String, nullable=True, default="")
    password_hash = Column(String)
    role = Column(Enum(RoleEnum), default=RoleEnum.dispatcher)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))

    buildings = relationship("Building", back_populates="owner")
    assigned_tickets = relationship("Ticket", back_populates="assignee")


class Building(Base):
    __tablename__ = "buildings"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    address = Column(String)
    owner_id = Column(String, ForeignKey("users.id"))

    owner = relationship("User", back_populates="buildings")
    tenants = relationship("Tenant", back_populates="building")


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    phone = Column(String, unique=True, index=True)
    building_id = Column(Integer, ForeignKey("buildings.id"))
    apartment = Column(String)

    building = relationship("Building", back_populates="tenants")
    conversations = relationship("Conversation", back_populates="tenant")
    tickets = relationship("Ticket", back_populates="tenant")


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"))
    whatsapp_chat_id = Column(String, unique=True, index=True)
    status = Column(Enum(ConversationStatusEnum), default=ConversationStatusEnum.open)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))

    tenant = relationship("Tenant", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"))
    sender = Column(Enum(MessageSenderEnum))
    message_type = Column(Enum(MessageTypeEnum))
    content = Column(Text, nullable=True) # Text or caption
    media_url = Column(Text, nullable=True) # JSON array string of urls or single url
    created_at = Column(DateTime, default=datetime.now(timezone.utc))

    conversation = relationship("Conversation", back_populates="messages")


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, index=True)
    ticket_number = Column(String, unique=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"))
    category = Column(String)
    urgency = Column(String)
    description = Column(Text)
    photo_url = Column(Text, nullable=True)
    availability_time = Column(String)
    assigned_to = Column(String, ForeignKey("users.id"), nullable=True)
    status = Column(Enum(TicketStatusEnum), default=TicketStatusEnum.new)
    scheduled_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))

    tenant = relationship("Tenant", back_populates="tickets")
    assignee = relationship("User", back_populates="assigned_tickets")
    notes = relationship("TicketNote", back_populates="ticket", cascade="all, delete-orphan")


class TicketNote(Base):
    __tablename__ = "ticket_notes"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"))
    author_id = Column(String, ForeignKey("users.id"))
    text = Column(Text)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))

    ticket = relationship("Ticket", back_populates="notes")
    author = relationship("User")
