"""Secure credential generation and environment variable injection for database containers."""

import secrets
import string
from dataclasses import dataclass
from typing import Optional

from cryptography.fernet import Fernet

from core.config import settings


@dataclass
class DBCredentials:
    user: str
    password: str
    db_name: str


@dataclass
class DBInfo:
    container_id: str
    container_name: str
    host: str
    port: int
    db_name: str
    user: str
    password: str


class CredentialManager:
    """Generates and manages database credentials for sandboxed jobs."""

    @staticmethod
    def generate(db_type: str) -> DBCredentials:
        """Generate random credentials for a database container."""
        alpha = string.ascii_letters + string.digits
        password = "".join(secrets.choice(alpha) for _ in range(24))
        user = f"gd_{secrets.token_hex(4)}"
        db_name = f"app_{secrets.token_hex(4)}"
        return DBCredentials(user=user, password=password, db_name=db_name)

    @staticmethod
    def build_container_env(db_type: str, creds: DBCredentials) -> dict[str, str]:
        """Build environment variables to inject INTO the database container for initialization."""
        if db_type == "postgresql":
            return {
                "POSTGRES_USER": creds.user,
                "POSTGRES_PASSWORD": creds.password,
                "POSTGRES_DB": creds.db_name,
            }
        elif db_type in ("mysql", "mariadb"):
            return {
                "MYSQL_ROOT_PASSWORD": creds.password,
                "MYSQL_DATABASE": creds.db_name,
                "MYSQL_USER": creds.user,
                "MYSQL_PASSWORD": creds.password,
            }
        elif db_type == "mongodb":
            return {
                "MONGO_INITDB_ROOT_USERNAME": creds.user,
                "MONGO_INITDB_ROOT_PASSWORD": creds.password,
                "MONGO_INITDB_DATABASE": creds.db_name,
            }
        elif db_type == "redis":
            return {}  # Password passed via --requirepass in command args
        return {}

    @staticmethod
    def build_env_map(db_type: str, db_info: DBInfo) -> dict[str, str]:
        """Build environment variables to inject INTO the app container.

        Covers all popular framework naming conventions so the repo code
        can connect without any manual configuration.
        """
        h = db_info.host
        port = db_info.port
        u = db_info.user
        pw = db_info.password
        name = db_info.db_name

        if db_type == "postgresql":
            url = f"postgresql://{u}:{pw}@{h}:{port}/{name}"
            return {
                "DATABASE_URL": url,
                "DB_URL": url,
                "DB_HOST": h,
                "DB_PORT": str(port),
                "DB_NAME": name,
                "DB_DATABASE": name,
                "DB_USER": u,
                "DB_USERNAME": u,
                "DB_PASSWORD": pw,
                "POSTGRES_HOST": h,
                "POSTGRES_PORT": str(port),
                "POSTGRES_DB": name,
                "POSTGRES_USER": u,
                "POSTGRES_PASSWORD": pw,
                "PGHOST": h,
                "PGPORT": str(port),
                "PGDATABASE": name,
                "PGUSER": u,
                "PGPASSWORD": pw,
                "DJANGO_DB_ENGINE": "django.db.backends.postgresql",
                "DJANGO_DB_HOST": h,
                "DJANGO_DB_PORT": str(port),
                "DJANGO_DB_NAME": name,
                "DJANGO_DB_USER": u,
                "DJANGO_DB_PASSWORD": pw,
            }
        elif db_type in ("mysql", "mariadb"):
            url = f"mysql://{u}:{pw}@{h}:{port}/{name}"
            return {
                "DATABASE_URL": url,
                "DB_URL": url,
                "DB_HOST": h,
                "DB_PORT": str(port),
                "DB_NAME": name,
                "DB_DATABASE": name,
                "DB_USER": u,
                "DB_USERNAME": u,
                "DB_PASSWORD": pw,
                "MYSQL_HOST": h,
                "MYSQL_PORT": str(port),
                "MYSQL_DATABASE": name,
                "MYSQL_USER": u,
                "MYSQL_PASSWORD": pw,
            }
        elif db_type == "mongodb":
            url = f"mongodb://{u}:{pw}@{h}:{port}/{name}?authSource=admin"
            return {
                "MONGODB_URI": url,
                "MONGO_URI": url,
                "MONGO_URL": url,
                "DATABASE_URL": url,
                "MONGO_HOST": h,
                "MONGO_PORT": str(port),
                "MONGO_DB": name,
                "MONGO_DATABASE": name,
                "MONGO_USER": u,
                "MONGO_USERNAME": u,
                "MONGO_PASSWORD": pw,
            }
        elif db_type == "redis":
            url = f"redis://:{pw}@{h}:{port}/0"
            return {
                "REDIS_URL": url,
                "REDIS_URI": url,
                "REDIS_HOST": h,
                "REDIS_PORT": str(port),
                "REDIS_PASSWORD": pw,
                "CELERY_BROKER_URL": url,
                "CACHE_URL": url,
            }
        return {}

    @staticmethod
    def encrypt_password(password: str) -> str:
        """Encrypt a password using Fernet symmetric encryption."""
        key = settings.SECRET_KEY.encode() if isinstance(settings.SECRET_KEY, str) else settings.SECRET_KEY
        f = Fernet(key)
        return f.encrypt(password.encode()).decode()

    @staticmethod
    def decrypt_password(encrypted: str) -> str:
        """Decrypt a Fernet-encrypted password."""
        key = settings.SECRET_KEY.encode() if isinstance(settings.SECRET_KEY, str) else settings.SECRET_KEY
        f = Fernet(key)
        return f.decrypt(encrypted.encode()).decode()

    @staticmethod
    def mask_env_vars(env_vars: dict[str, str]) -> dict[str, str]:
        """Return a copy of env_vars with password values masked as '****'."""
        password_keys = {
            "DB_PASSWORD", "POSTGRES_PASSWORD", "PGPASSWORD", "MYSQL_PASSWORD",
            "MYSQL_ROOT_PASSWORD", "MONGO_PASSWORD", "REDIS_PASSWORD",
            "DJANGO_DB_PASSWORD",
        }
        masked = {}
        for key, value in env_vars.items():
            if key in password_keys:
                masked[key] = "****"
            elif "PASSWORD" in key.upper():
                masked[key] = "****"
            elif any(f":{value}@" in v for v in env_vars.values() if isinstance(v, str)):
                # Mask URL-style values that contain the raw password
                masked[key] = value.replace(
                    next(
                        (env_vars[k] for k in password_keys if k in env_vars),
                        "",
                    ),
                    "****",
                ) if any(k in env_vars for k in password_keys) else value
            else:
                masked[key] = value

        # Also mask passwords embedded in URLs
        for key in masked:
            if "URL" in key.upper() or "URI" in key.upper():
                for pw_key in password_keys:
                    if pw_key in env_vars:
                        masked[key] = masked[key].replace(env_vars[pw_key], "****")

        return masked
