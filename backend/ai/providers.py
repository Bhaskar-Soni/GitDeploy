"""AI provider definitions for GitDeploy.

Each provider has:
- name: Display name
- api_type: "openai" | "anthropic" | "gemini" (determines how to call the API)
- base_url: API base URL
- models: List of available models with id and name
- free: Whether the provider offers free usage
- key_url: Where to get an API key
"""

PROVIDERS: dict[str, dict] = {
    # ── FREE PROVIDERS ────────────────────────────────────────────────
    "gemini": {
        "name": "Google Gemini",
        "api_type": "gemini",
        "free": True,
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "models": [
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
            {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash"},
        ],
        "key_url": "https://aistudio.google.com/apikey",
    },
    "groq": {
        "name": "Groq",
        "api_type": "openai",
        "free": True,
        "base_url": "https://api.groq.com/openai/v1",
        "models": [
            {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B"},
            {"id": "deepseek-r1-distill-llama-70b", "name": "DeepSeek R1 70B"},
        ],
        "key_url": "https://console.groq.com/keys",
    },
    "cerebras": {
        "name": "Cerebras",
        "api_type": "openai",
        "free": True,
        "base_url": "https://api.cerebras.ai/v1",
        "models": [
            {"id": "llama-3.3-70b", "name": "Llama 3.3 70B"},
        ],
        "key_url": "https://cloud.cerebras.ai/",
    },
    "sambanova": {
        "name": "SambaNova",
        "api_type": "openai",
        "free": True,
        "base_url": "https://api.sambanova.ai/v1",
        "models": [
            {"id": "Meta-Llama-3.1-70B-Instruct", "name": "Llama 3.1 70B"},
        ],
        "key_url": "https://cloud.sambanova.ai/apis",
    },
    "github": {
        "name": "GitHub Models",
        "api_type": "openai",
        "free": True,
        "base_url": "https://models.inference.ai.azure.com",
        "models": [
            {"id": "gpt-4o", "name": "GPT-4o"},
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
        ],
        "key_url": "https://github.com/settings/tokens",
    },
    "huggingface": {
        "name": "HuggingFace",
        "api_type": "openai",
        "free": True,
        "base_url": "https://api-inference.huggingface.co/v1",
        "models": [
            {"id": "Qwen/Qwen2.5-72B-Instruct", "name": "Qwen 2.5 72B"},
            {"id": "meta-llama/Llama-3.3-70B-Instruct", "name": "Llama 3.3 70B"},
        ],
        "key_url": "https://huggingface.co/settings/tokens",
    },
    "nvidia": {
        "name": "Nvidia NIM",
        "api_type": "openai",
        "free": True,
        "base_url": "https://integrate.api.nvidia.com/v1",
        "models": [
            {"id": "meta/llama-3.1-70b-instruct", "name": "Llama 3.1 70B"},
        ],
        "key_url": "https://build.nvidia.com/",
    },

    # ── PAID PROVIDERS ────────────────────────────────────────────────
    "anthropic": {
        "name": "Anthropic",
        "api_type": "anthropic",
        "free": False,
        "base_url": "https://api.anthropic.com",
        "models": [
            {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4"},
            {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5"},
        ],
        "key_url": "https://console.anthropic.com/settings/keys",
    },
    "openai": {
        "name": "OpenAI",
        "api_type": "openai",
        "free": False,
        "base_url": "https://api.openai.com/v1",
        "models": [
            {"id": "gpt-4o", "name": "GPT-4o"},
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
        ],
        "key_url": "https://platform.openai.com/api-keys",
    },
    "deepseek": {
        "name": "DeepSeek",
        "api_type": "openai",
        "free": False,
        "base_url": "https://api.deepseek.com",
        "models": [
            {"id": "deepseek-chat", "name": "DeepSeek V3"},
            {"id": "deepseek-reasoner", "name": "DeepSeek R1"},
        ],
        "key_url": "https://platform.deepseek.com/api_keys",
    },
    "mistral": {
        "name": "Mistral AI",
        "api_type": "openai",
        "free": False,
        "base_url": "https://api.mistral.ai/v1",
        "models": [
            {"id": "mistral-large-latest", "name": "Mistral Large"},
            {"id": "mistral-small-latest", "name": "Mistral Small"},
        ],
        "key_url": "https://console.mistral.ai/api-keys",
    },
    "together": {
        "name": "Together AI",
        "api_type": "openai",
        "free": False,
        "base_url": "https://api.together.xyz/v1",
        "models": [
            {"id": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "name": "Llama 3.3 70B Turbo"},
        ],
        "key_url": "https://api.together.ai/settings/api-keys",
    },
    "openrouter": {
        "name": "OpenRouter",
        "api_type": "openai",
        "free": False,
        "base_url": "https://openrouter.ai/api/v1",
        "models": [
            {"id": "anthropic/claude-sonnet-4-20250514", "name": "Claude Sonnet 4 (via OR)"},
            {"id": "google/gemini-2.5-flash", "name": "Gemini 2.5 Flash (via OR)"},
            {"id": "auto", "name": "Auto (best available)"},
        ],
        "key_url": "https://openrouter.ai/keys",
    },
    "fireworks": {
        "name": "Fireworks AI",
        "api_type": "openai",
        "free": False,
        "base_url": "https://api.fireworks.ai/inference/v1",
        "models": [
            {"id": "accounts/fireworks/models/llama-v3p1-70b-instruct", "name": "Llama 3.1 70B"},
        ],
        "key_url": "https://fireworks.ai/api-keys",
    },
    "xai": {
        "name": "xAI",
        "api_type": "openai",
        "free": False,
        "base_url": "https://api.x.ai/v1",
        "models": [
            {"id": "grok-2", "name": "Grok 2"},
        ],
        "key_url": "https://console.x.ai/",
    },
}


def get_provider(provider_id: str) -> dict | None:
    return PROVIDERS.get(provider_id)


def list_providers() -> list[dict]:
    """Return provider list for the settings UI."""
    result = []
    for pid, p in PROVIDERS.items():
        result.append({
            "id": pid,
            "name": p["name"],
            "free": p["free"],
            "models": p["models"],
            "key_url": p["key_url"],
        })
    return result
