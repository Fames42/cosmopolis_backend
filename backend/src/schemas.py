from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Any
from datetime import datetime
from uuid import UUID

from .models import RoleEnum, MessageSenderEnum, MessageTypeEnum, TicketStatusEnum, ConversationStatusEnum

# --- User Schemas ---
class UserBase(BaseModel):
    name: str
    email: EmailStr
    role: RoleEnum = RoleEnum.dispatcher

class UserCreate(UserBase):
    password: str

class UserResponse(UserBase):
    id: str
    created_at: datetime
    
    class Config:
        from_attributes = True

class TechnicianResponse(BaseModel):
    id: str
    name: str
    email: str = ""
    phone: str = ""
    activeTickets: int = 0
    status: str = "ACTIVE"

    class Config:
        from_attributes = True

class TechnicianCreate(BaseModel):
    name: str
    email: EmailStr
    phone: str = ""
    password: str

class TechnicianUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None

# --- Building Schemas ---
class BuildingBase(BaseModel):
    name: str
    address: str

class BuildingCreate(BuildingBase):
    owner_id: str

class BuildingResponse(BuildingBase):
    id: int
    owner_id: str
    
    class Config:
        from_attributes = True

# --- Tenant Schemas ---
class TenantBase(BaseModel):
    name: str
    phone: str
    apartment: str

class TenantCreate(TenantBase):
    building_id: int

class TenantResponse(TenantBase):
    id: int
    building_id: int
    
    class Config:
        from_attributes = True

# --- Conversation & Message Schemas ---
class MessageBase(BaseModel):
    sender: MessageSenderEnum
    message_type: MessageTypeEnum
    content: Optional[str] = None
    media_url: Optional[str] = None

class MessageCreate(MessageBase):
    pass

class MessageResponse(MessageBase):
    id: int
    conversation_id: int
    created_at: datetime

    class Config:
        from_attributes = True

class ConversationBase(BaseModel):
    whatsapp_chat_id: str
    status: ConversationStatusEnum = ConversationStatusEnum.open

class ConversationCreate(ConversationBase):
    tenant_id: int

class ConversationResponse(ConversationBase):
    id: int
    tenant_id: int
    created_at: datetime
    messages: List[MessageResponse] = []

    class Config:
        from_attributes = True

# --- Ticket Schemas ---
class TicketBase(BaseModel):
    category: str
    urgency: str
    description: str
    photo_url: Optional[str] = None
    availability_time: str
    status: TicketStatusEnum = TicketStatusEnum.new
    scheduled_time: Optional[datetime] = None

class TicketCreate(TicketBase):
    tenant_id: int

class TicketUpdate(BaseModel):
    category: Optional[str] = None
    urgency: Optional[str] = None
    description: Optional[str] = None
    photo_url: Optional[str] = None
    availability_time: Optional[str] = None
    assigned_to: Optional[str] = None
    status: Optional[TicketStatusEnum] = None
    scheduled_time: Optional[datetime] = None

class TicketResponse(TicketBase):
    id: int
    ticket_number: str
    tenant_id: int
    assigned_to: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# --- Auth Schemas ---
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UserAuthInfo(BaseModel):
    id: str
    email: str
    role: str

class LoginResponse(BaseModel):
    token: str
    user: UserAuthInfo

# --- Note Schemas ---
class TicketNoteSchema(BaseModel):
    id: int
    author: str
    time: str
    text: str
    role: str

    class Config:
        from_attributes = True

class NoteCreate(BaseModel):
    text: str

# --- Dispatcher Ticket Schemas ---
class TicketDispatcherListResponse(BaseModel):
    id: str
    category: str
    urgency: str
    tenant: str
    assignedTo: Optional[str] = None
    status: str
    scheduled: Optional[str] = None
    created: str

class TenantInfoSchema(BaseModel):
    name: str
    phone: str
    address: str
    apartment: str

class IssueDetailsSchema(BaseModel):
    category: str
    urgency: str
    description: str

class TicketDispatcherDetailResponse(BaseModel):
    id: str
    ticketStatus: str
    assignedTech: Optional[str] = None
    scheduledDate: Optional[str] = None
    created: str
    tenantInfo: TenantInfoSchema
    issueDetails: IssueDetailsSchema
    notes: List[TicketNoteSchema] = []

# --- Technician Ticket Schemas ---
class TicketTechnicianListResponse(BaseModel):
    id: str
    category: str
    address: str
    urgency: str
    scheduled: Optional[str] = None
    status: str
    isToday: bool = False

class TicketCommentSchema(BaseModel):
    id: int
    text: str

class TicketTechnicianDetailResponse(BaseModel):
    id: str
    category: str
    urgency: str
    address: str
    description: str
    tenantPhone: str
    status: str
    comments: List[TicketCommentSchema] = []

class TicketUpdateStatus(BaseModel):
    status: str
