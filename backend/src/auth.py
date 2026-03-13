from datetime import datetime, timedelta, timezone
from typing import Optional
import os
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from jose import JWTError, jwt
from passlib.context import CryptContext

from . import models, schemas
from .database import get_db

# Security Configuration
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    import warnings
    warnings.warn("SECRET_KEY not set — using insecure default. Set SECRET_KEY env var for production.", stacklevel=1)
    SECRET_KEY = "super_secret_key_change_in_production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 days

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

router = APIRouter()

# --- Password Utils ---
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

# --- JWT Utils ---
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# --- Dependencies ---
def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
        
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise credentials_exception
    return user

# RBAC Dependencies
def check_role(allowed_roles: list[models.RoleEnum]):
    def role_checker(current_user: models.User = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions to access this resource"
            )
        return current_user
    return role_checker

# Helper for standard role scopes
get_admin_user = check_role([models.RoleEnum.admin])
get_dispatcher_user = check_role([models.RoleEnum.admin, models.RoleEnum.dispatcher])
get_owner_user = check_role([models.RoleEnum.admin, models.RoleEnum.owner])
get_technician_user = check_role([models.RoleEnum.admin, models.RoleEnum.dispatcher, models.RoleEnum.technician, models.RoleEnum.owner])
get_agent_user = check_role([models.RoleEnum.admin, models.RoleEnum.agent])

# --- Auth Endpoints ---
@router.post("/token")
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.id, "role": user.role.value}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer", "user": {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role.value
    }}

@router.post("/login", response_model=schemas.LoginResponse)
def login(login_data: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == login_data.email).first()
    if not user or not verify_password(login_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.id, "role": user.role.value}, expires_delta=access_token_expires
    )
    return {
        "token": access_token,
        "user": {
            "id": user.id,
            "email": user.email,
            "role": user.role.value.upper()
        }
    }

@router.get("/me", response_model=schemas.UserResponse)
def read_users_me(current_user: models.User = Depends(get_current_user)):
    return current_user
