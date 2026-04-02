"""REST endpoints for AI provider settings."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from db.database import get_db
from db.models import AppSetting
from ai.providers import list_providers

router = APIRouter(tags=["settings"])


class AISettingsPayload(BaseModel):
    provider: str
    api_key: str
    model: Optional[str] = None


class AISettingsResponse(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    has_key: bool = False


@router.get("/api/settings/providers")
async def get_providers():
    """List all available AI providers and their models."""
    return list_providers()


@router.get("/api/settings/ai", response_model=AISettingsResponse)
async def get_ai_settings(db: AsyncSession = Depends(get_db)):
    """Get current AI configuration (key is never returned)."""
    provider = await db.get(AppSetting, "ai_provider")
    api_key = await db.get(AppSetting, "ai_api_key")
    model = await db.get(AppSetting, "ai_model")

    return AISettingsResponse(
        provider=provider.value if provider else None,
        model=model.value if model else None,
        has_key=bool(api_key),
    )


@router.post("/api/settings/ai")
async def save_ai_settings(payload: AISettingsPayload, db: AsyncSession = Depends(get_db)):
    """Save AI provider, API key, and model selection."""
    for key, value in [
        ("ai_provider", payload.provider),
        ("ai_api_key", payload.api_key),
        ("ai_model", payload.model or ""),
    ]:
        existing = await db.get(AppSetting, key)
        if existing:
            existing.value = value
        else:
            db.add(AppSetting(key=key, value=value))

    await db.flush()
    await db.commit()

    return {"message": "AI settings saved"}


@router.delete("/api/settings/ai")
async def delete_ai_settings(db: AsyncSession = Depends(get_db)):
    """Remove AI provider configuration and API key."""
    for key in ("ai_provider", "ai_api_key", "ai_model"):
        existing = await db.get(AppSetting, key)
        if existing:
            await db.delete(existing)
    await db.flush()
    await db.commit()
    return {"message": "AI settings removed"}


@router.post("/api/settings/ai/test")
async def test_ai_settings(payload: AISettingsPayload):
    """Test the AI connection with the provided credentials."""
    from ai.providers import get_provider
    from ai.ai_client import AIClient
    import json
    import requests as req

    provider = get_provider(payload.provider)
    if not provider:
        return {"success": False, "error": f"Unknown provider: {payload.provider}"}

    model = payload.model or provider["models"][0]["id"]

    try:
        # Build a simple test call
        test_prompt = 'Return exactly this JSON: {"status": "ok", "message": "GitDeploy AI connected"}'

        if provider["api_type"] == "openai":
            resp = req.post(
                f"{provider['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {payload.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": test_prompt}],
                    "temperature": 0,
                    "max_tokens": 50,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        elif provider["api_type"] == "anthropic":
            resp = req.post(
                f"{provider['base_url']}/v1/messages",
                headers={
                    "x-api-key": payload.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 50,
                    "messages": [{"role": "user", "content": test_prompt}],
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get("content", [{}])[0].get("text", "")

        elif provider["api_type"] == "gemini":
            resp = req.post(
                f"{provider['base_url']}/models/{model}:generateContent?key={payload.api_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": test_prompt}]}],
                    "generationConfig": {"maxOutputTokens": 50},
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")

        else:
            return {"success": False, "error": "Unknown API type"}

        return {"success": True, "response": content.strip()[:200]}

    except req.HTTPError as e:
        error_body = ""
        try:
            error_body = e.response.json().get("error", {}).get("message", str(e))
        except Exception:
            error_body = str(e)
        return {"success": False, "error": error_body}
    except Exception as e:
        return {"success": False, "error": str(e)}
