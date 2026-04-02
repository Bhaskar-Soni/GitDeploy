"""FastAPI application entry point for GitDeploy."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from api.deps import get_current_user
from api.routes import jobs, ws, terminal, proxy, auth
from api.routes import settings as settings_routes
from core.config import settings
from core.auth import hash_password
from db.database import async_engine, AsyncSessionLocal, Base
from db.models import User

logger = logging.getLogger(__name__)


async def _seed_admin():
    """Create default admin user if no users exist."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).limit(1))
        if result.scalar_one_or_none() is None:
            admin = User(
                username=settings.DEFAULT_ADMIN_USER,
                password_hash=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
            )
            session.add(admin)
            await session.commit()
            logger.warning(
                "Created default admin user '%s' with default password. "
                "Change it via Settings or set DEFAULT_ADMIN_PASSWORD env var.",
                settings.DEFAULT_ADMIN_USER,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables on startup if they don't exist."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _seed_admin()
    yield


app = FastAPI(
    title="GitDeploy",
    description="Automatically install, build, and run any GitHub repository in an isolated Docker sandbox.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Public routes (no auth)
app.include_router(auth.router)
app.include_router(proxy.router)  # Proxy serves deployed apps to external users

# Protected routes (require JWT)
auth_dep = [Depends(get_current_user)]
app.include_router(jobs.router, prefix="/api", dependencies=auth_dep)
app.include_router(settings_routes.router, dependencies=auth_dep)

# WebSocket routes handle their own auth via query param
app.include_router(ws.router)
app.include_router(terminal.router)


@app.get("/")
async def root():
    return RedirectResponse(url="/docs")


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "gitdeploy"}
