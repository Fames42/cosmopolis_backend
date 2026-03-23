from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Optional

from .. import models
from ..database import get_db
from ..auth import get_agent_user

router = APIRouter()


# --- Schemas ---

class BuildingCreateRequest(BaseModel):
    name: str
    address: str

class BuildingListItem(BaseModel):
    id: int
    name: str
    address: str
    tenant_count: int

class TenantCreateRequest(BaseModel):
    name: str
    phone: str
    apartment: str
    building_id: Optional[int] = None
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
    category: Optional[str] = None
    company: Optional[str] = None
    agent_enabled: bool = True

class TenantUpdateRequest(BaseModel):
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
    category: Optional[str] = None
    company: Optional[str] = None
    agent_enabled: Optional[bool] = None

class TenantListItem(BaseModel):
    id: int
    name: str
    phone: str
    apartment: str
    building_id: Optional[int] = None
    building_name: Optional[str] = None
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
    category: Optional[str] = None
    company: Optional[str] = None
    agent_enabled: bool = True

class TenantAgentSupportRequest(BaseModel):
    enabled: bool

class TenantAssignRequest(BaseModel):
    building_id: int


# --- Buildings ---

@router.get("/buildings", response_model=list[BuildingListItem])
def list_buildings(
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(get_agent_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(
            models.Building.id,
            models.Building.name,
            models.Building.address,
            func.count(models.Tenant.id).label("tenant_count"),
        )
        .outerjoin(models.Tenant, models.Tenant.building_id == models.Building.id)
        .group_by(models.Building.id)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [
        BuildingListItem(id=r.id, name=r.name, address=r.address, tenant_count=r.tenant_count)
        for r in rows
    ]


@router.post("/buildings", response_model=BuildingListItem)
def create_building(
    body: BuildingCreateRequest,
    current_user: models.User = Depends(get_agent_user),
    db: Session = Depends(get_db),
):
    building = models.Building(
        name=body.name,
        address=body.address,
        owner_id=current_user.id,
    )
    db.add(building)
    db.commit()
    db.refresh(building)
    return BuildingListItem(
        id=building.id,
        name=building.name,
        address=building.address,
        tenant_count=0,
    )


def _tenant_to_item(tenant: models.Tenant, building_name: str | None = None) -> TenantListItem:
    return TenantListItem(
        id=tenant.id,
        name=tenant.name,
        phone=tenant.phone,
        apartment=tenant.apartment,
        building_id=tenant.building_id,
        building_name=building_name,
        email=tenant.email,
        lease_start_date=tenant.lease_start_date,
        lease_end_date=tenant.lease_end_date,
        adults=tenant.adults,
        children=tenant.children,
        has_pets=tenant.has_pets,
        parking=tenant.parking,
        parking_slot=tenant.parking_slot,
        emergency_contact=tenant.emergency_contact,
        notes=tenant.notes,
        category=tenant.category,
        company=tenant.company,
        agent_enabled=tenant.agent_enabled,
    )


# --- Tenants ---

@router.get("/tenants", response_model=list[TenantListItem])
def list_tenants(
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(get_agent_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(models.Tenant, models.Building.name.label("building_name"))
        .outerjoin(models.Building, models.Tenant.building_id == models.Building.id)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [
        _tenant_to_item(tenant, bname)
        for tenant, bname in rows
    ]


@router.post("/tenants", response_model=TenantListItem)
def create_tenant(
    body: TenantCreateRequest,
    current_user: models.User = Depends(get_agent_user),
    db: Session = Depends(get_db),
):
    if body.building_id:
        building = db.query(models.Building).filter(models.Building.id == body.building_id).first()
        if not building:
            raise HTTPException(status_code=404, detail="Building not found")

    existing = db.query(models.Tenant).filter(models.Tenant.phone == body.phone).first()
    if existing:
        raise HTTPException(status_code=400, detail="Tenant with this phone already exists")

    tenant = models.Tenant(
        name=body.name,
        phone=body.phone,
        apartment=body.apartment,
        building_id=body.building_id,
        email=body.email,
        lease_start_date=body.lease_start_date,
        lease_end_date=body.lease_end_date,
        adults=body.adults,
        children=body.children,
        has_pets=body.has_pets,
        parking=body.parking,
        parking_slot=body.parking_slot,
        emergency_contact=body.emergency_contact,
        notes=body.notes,
        category=body.category,
        company=body.company,
        agent_enabled=body.agent_enabled,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    building_name = None
    if tenant.building_id:
        building_name = db.query(models.Building.name).filter(models.Building.id == tenant.building_id).scalar()

    return _tenant_to_item(tenant, building_name)


@router.put("/tenants/{tenant_id}", response_model=TenantListItem)
def update_tenant(
    tenant_id: int,
    body: TenantUpdateRequest,
    current_user: models.User = Depends(get_agent_user),
    db: Session = Depends(get_db),
):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(tenant, field, value)

    db.commit()
    db.refresh(tenant)

    building_name = None
    if tenant.building_id:
        building_name = db.query(models.Building.name).filter(models.Building.id == tenant.building_id).scalar()

    return _tenant_to_item(tenant, building_name)


@router.delete("/tenants/{tenant_id}")
def delete_tenant(
    tenant_id: int,
    current_user: models.User = Depends(get_agent_user),
    db: Session = Depends(get_db),
):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    db.delete(tenant)
    db.commit()
    return {"detail": "Tenant deleted"}


@router.patch("/tenants/{tenant_id}/agent-support", response_model=TenantListItem)
def toggle_agent_support(
    tenant_id: int,
    body: TenantAgentSupportRequest,
    current_user: models.User = Depends(get_agent_user),
    db: Session = Depends(get_db),
):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.agent_enabled = body.enabled
    db.commit()
    db.refresh(tenant)

    building_name = None
    if tenant.building_id:
        building_name = db.query(models.Building.name).filter(models.Building.id == tenant.building_id).scalar()

    return _tenant_to_item(tenant, building_name)


@router.put("/tenants/{tenant_id}/assign", response_model=TenantListItem)
def assign_tenant(
    tenant_id: int,
    body: TenantAssignRequest,
    current_user: models.User = Depends(get_agent_user),
    db: Session = Depends(get_db),
):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    building = db.query(models.Building).filter(models.Building.id == body.building_id).first()
    if not building:
        raise HTTPException(status_code=404, detail="Building not found")

    tenant.building_id = body.building_id
    db.commit()
    db.refresh(tenant)

    return _tenant_to_item(tenant, building.name)
