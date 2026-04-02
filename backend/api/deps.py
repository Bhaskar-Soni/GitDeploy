"""FastAPI dependencies for authentication."""

from fastapi import Depends, HTTPException, status, WebSocket, Query
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import decode_access_token
from db.database import get_db, AsyncSessionLocal
from db.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Validate JWT and return the current user."""
    username = decode_access_token(token)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    return user


async def ws_authenticate(websocket: WebSocket, token: str | None = None) -> bool:
    """Authenticate a WebSocket connection via query param token.

    Must be called BEFORE websocket.accept(). On failure, accepts then
    immediately closes with an error code so the client gets feedback.
    """
    if not token:
        await websocket.accept()
        await websocket.close(code=4001, reason="Missing token")
        return False
    username = decode_access_token(token)
    if not username:
        await websocket.accept()
        await websocket.close(code=4001, reason="Invalid token")
        return False
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            await websocket.accept()
            await websocket.close(code=4001, reason="User not found")
            return False
    return True
