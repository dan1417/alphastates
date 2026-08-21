"""Authentication API routes."""

import logging
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import (
    create_access_token,
    get_current_user,
    get_password_hash,
    verify_password,
)
from src.common.config import settings
from src.common.db import get_session
from src.common.models import User

logger = logging.getLogger(__name__)

router = APIRouter()


# Request/Response models
class SignupRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    email: str
    created_at: str
    is_active: bool


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    request: SignupRequest,
    session: AsyncSession = Depends(get_session)
):
    """Create a new user account."""
    try:
        # Check if user already exists
        result = await session.execute(
            select(User).where(User.email == request.email)
        )
        existing_user = result.scalar_one_or_none()
        
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        
        # Validate password strength
        if len(request.password) < 8:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 8 characters long"
            )
        
        # Bcrypt has a 72-byte limit
        if len(request.password.encode('utf-8')) > 72:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password is too long (maximum 72 bytes)"
            )
        
        # Create new user
        hashed_password = get_password_hash(request.password)
        new_user = User(
            email=request.email,
            hashed_password=hashed_password,
            created_at=datetime.utcnow()
        )
        
        session.add(new_user)
        await session.commit()
        await session.refresh(new_user)
        
        # Create access token
        access_token = create_access_token(
            data={"sub": new_user.email},
            expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes)
        )
        
        logger.info(f"New user registered: {new_user.email}")
        
        return TokenResponse(access_token=access_token)
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        # Log any unexpected errors
        logger.error(f"Error during signup: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during signup"
        )


@router.post("/login", response_model=TokenResponse)
async def login(
    request: LoginRequest,
    session: AsyncSession = Depends(get_session)
):
    """Login and get access token."""
    try:
        # Find user
        result = await session.execute(
            select(User).where(User.email == request.email)
        )
        user = result.scalar_one_or_none()
        
        # Verify credentials
        if not user or not verify_password(request.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is disabled"
            )
        
        # Create access token
        access_token = create_access_token(
            data={"sub": user.email},
            expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes)
        )
        
        logger.info(f"User logged in: {user.email}")
        
        return TokenResponse(access_token=access_token)
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        # Log any unexpected errors
        logger.error(f"Error during login: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during login"
        )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user information."""
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        created_at=current_user.created_at.isoformat(),
        is_active=current_user.is_active
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(current_user: User = Depends(get_current_user)):
    """Refresh access token."""
    access_token = create_access_token(
        data={"sub": current_user.email},
        expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    
    return TokenResponse(access_token=access_token)


def generate_random_username() -> str:
    """Generate a random username (word/phrase)."""
    # Generate a random phrase like "silent-moon-1234" or "brave-ocean-5678"
    adjectives = [
        "silent", "brave", "swift", "calm", "bright", "dark", "gentle", "wild",
        "ancient", "modern", "cosmic", "oceanic", "mountain", "forest", "desert",
        "stellar", "lunar", "solar", "crystal", "golden", "silver", "crimson",
        "azure", "emerald", "violet", "amber", "pearl", "diamond", "ruby"
    ]
    nouns = [
        "moon", "ocean", "mountain", "forest", "river", "star", "cloud", "wind",
        "fire", "earth", "stone", "crystal", "light", "shadow", "wave", "storm",
        "thunder", "lightning", "rainbow", "sunset", "dawn", "dusk", "horizon",
        "valley", "peak", "canyon", "meadow", "grove", "glade", "stream"
    ]
    
    adjective = secrets.choice(adjectives)
    noun = secrets.choice(nouns)
    number = secrets.randbelow(10000)
    
    return f"{adjective}-{noun}-{number}"


async def ensure_unique_username(session: AsyncSession, base_username: str) -> str:
    """Ensure the username is unique by appending a suffix if needed."""
    username = base_username
    max_attempts = 100
    attempt = 0
    
    while attempt < max_attempts:
        result = await session.execute(
            select(User).where(User.email == username)
        )
        existing_user = result.scalar_one_or_none()
        
        if existing_user is None:
            return username
        
        # Username exists, append a random suffix
        suffix = secrets.randbelow(1000000)
        username = f"{base_username}-{suffix}"
        attempt += 1
    
    # Fallback: use timestamp-based username
    timestamp = int(datetime.utcnow().timestamp())
    return f"{base_username}-{timestamp}"


@router.post("/anonymous", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def create_anonymous_user(
    session: AsyncSession = Depends(get_session)
):
    """Create an anonymous user account with random username and default password."""
    try:
        # Generate unique random username
        base_username = generate_random_username()
        username = await ensure_unique_username(session, base_username)
        
        # Create new anonymous user with password from config
        hashed_password = get_password_hash(settings.anonymous_default_password)
        new_user = User(
            email=username,  # Store username in email field
            hashed_password=hashed_password,
            created_at=datetime.utcnow()
        )
        
        session.add(new_user)
        await session.commit()
        await session.refresh(new_user)
        
        # Create access token
        access_token = create_access_token(
            data={"sub": new_user.email},
            expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes)
        )
        
        logger.info(f"Anonymous user created: {new_user.email}")
        
        return TokenResponse(access_token=access_token)
    except Exception as e:
        logger.error(f"Error creating anonymous user: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while creating anonymous account"
        )

