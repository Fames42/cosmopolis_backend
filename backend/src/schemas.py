from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Any
from datetime import datetime
from uuid import UUID

from .models import RoleEnum, MessageSenderEnum, MessageTypeEnum, TicketStatusEnum, ConversationStatusEnum, ConversationStateEnum, ScenarioEnum

# --- User Schemas ---
class UserBase(BaseModel):
    name: str
    email: EmailStr
    role: RoleEnum = RoleEnum.dispatcher

class UserCreate(UserBase):
    password: str
    phone: Optional[str] = None

class UserResponse(UserBase):
    id: str
    phone: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class TechnicianScheduleItem(BaseModel):
    day_of_week: int  # 0=Monday .. 6=Sunday
    start_time: str   # "09:00"
    end_time: str     # "18:00"

class TechnicianScheduleResponse(TechnicianScheduleItem):
    id: int
    technician_id: str

    class Config:
        from_attributes = True

class TechnicianScheduleBulkUpdate(BaseModel):
    schedules: List[TechnicianScheduleItem]

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
    email: Optional[str] = None
    lease_start_date: Optional[str] = None
    lease_end_date: Optional[str] = None
    adults: Optional[int] = None
    children: Optional[int] = None
    has_pets: Optional[bool] = None
    parking: Optional[bool] = None
    parking_slot: Optional[str] = None
    emergency_contact: Optional[str] = None
    notes: Optional[str] = None
    company: Optional[str] = None
    agent_enabled: bool = False

class TenantCreate(TenantBase):
    building_id: int

class TenantUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    apartment: Optional[str] = None
    email: Optional[str] = None
    lease_start_date: Optional[str] = None
    lease_end_date: Optional[str] = None
    adults: Optional[int] = None
    children: Optional[int] = None
    has_pets: Optional[bool] = None
    parking: Optional[bool] = None
    parking_slot: Optional[str] = None
    emergency_contact: Optional[str] = None
    notes: Optional[str] = None
    company: Optional[str] = None
    agent_enabled: Optional[bool] = None

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
    state: Optional[str] = None
    scenario: Optional[str] = None
    classifier_confidence: Optional[float] = None
    created_at: datetime
    messages: List[MessageResponse] = []

    class Config:
        from_attributes = True

# --- Ticket Schemas ---
class TicketBase(BaseModel):
    category: str
    urgency: str
    description: str
    photo_urls: Optional[List[str]] = None
    availability_time: str
    status: TicketStatusEnum = TicketStatusEnum.new
    scheduled_time: Optional[datetime] = None

class TicketCreate(TicketBase):
    tenant_id: int

class TicketUpdate(BaseModel):
    category: Optional[str] = None
    urgency: Optional[str] = None
    description: Optional[str] = None
    photo_urls: Optional[List[str]] = None
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
    photo_urls: Optional[List[str]] = None

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

# --- Manager Schedule Overview Schemas ---
class TechnicianScheduleOverview(BaseModel):
    technician_id: str
    technician_name: str
    schedules: List[TechnicianScheduleItem] = []

class TechnicianWorkloadItem(BaseModel):
    ticket_number: str
    category: str
    urgency: str
    status: str
    scheduled_time: Optional[datetime] = None
    description: str = ""

class TechnicianWorkloadResponse(BaseModel):
    technician_id: str
    technician_name: str
    tickets: List[TechnicianWorkloadItem] = []

# --- Agent result schema ---
from .agent.types import AgentResult

# Backward-compatible alias
AgentResponse = AgentResult

class TicketExportRequest(BaseModel):
    ticket_ids: List[str]

class TestMessageRequest(BaseModel):
    phone: str
    message: str

class TestMessageResponse(BaseModel):
    reply: str
    state: str
    agent_response: Optional[AgentResponse] = None
