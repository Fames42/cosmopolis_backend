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

class TenantListItem(BaseModel):
    id: int
    name: str
    phone: str
    apartment: str
    building_id: Optional[int] = None
    building_name: Optional[str] = None

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
        TenantListItem(
            id=tenant.id,
            name=tenant.name,
            phone=tenant.phone,
            apartment=tenant.apartment,
            building_id=tenant.building_id,
            building_name=bname,
        )
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
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    building_name = None
    if tenant.building_id:
        building_name = db.query(models.Building.name).filter(models.Building.id == tenant.building_id).scalar()

    return TenantListItem(
        id=tenant.id,
        name=tenant.name,
        phone=tenant.phone,
        apartment=tenant.apartment,
        building_id=tenant.building_id,
        building_name=building_name,
    )


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

    return TenantListItem(
        id=tenant.id,
        name=tenant.name,
        phone=tenant.phone,
        apartment=tenant.apartment,
        building_id=tenant.building_id,
        building_name=building.name,
    )
