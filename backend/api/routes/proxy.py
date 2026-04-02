"""Reverse proxy for running apps — strips X-Frame-Options so the UI iframe works."""

import re
from uuid import UUID

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response

from db.database import AsyncSessionLocal
from db.models import Job, JobStatus

router = APIRouter()

# Headers we strip from upstream responses so iframes are not blocked
_STRIP_RESPONSE_HEADERS = {
    "x-frame-options",
    "content-security-policy",
    "x-content-type-options",
}


@router.api_route("/api/proxy/{job_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy_app(job_id: str, path: str, request: Request):
    """Proxy a request to a running app container, stripping iframe-blocking headers."""
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID")

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_uuid)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != JobStatus.RUNNING or not job.proxy_port:
            raise HTTPException(status_code=503, detail="App is not running")
        proxy_port = job.proxy_port

    target_url = f"http://host.docker.internal:{proxy_port}/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    forward_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "connection", "transfer-encoding")
    }

    body = await request.body()

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        try:
            upstream = await client.request(
                method=request.method,
                url=target_url,
                headers=forward_headers,
                content=body,
            )
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="Cannot reach app container")
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="App container timed out")

    # Strip iframe-blocking headers and content-length (we may modify content)
    resp_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _STRIP_RESPONSE_HEADERS
        and k.lower() not in ("content-length", "content-encoding", "transfer-encoding")
    }
    # Prevent browser from caching proxied content (avoids showing stale/wrong app)
    resp_headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"

    # Rewrite Location headers to go through proxy
    if "location" in resp_headers:
        loc = resp_headers["location"]
        port_pattern = re.compile(rf"https?://[^/]*:{proxy_port}")
        resp_headers["location"] = port_pattern.sub(f"/api/proxy/{job_id}", loc)
        # Also handle absolute paths in redirects
        if loc.startswith("/") and not loc.startswith(f"/api/proxy/"):
            resp_headers["location"] = f"/api/proxy/{job_id}{loc}"

    content = upstream.content
    content_type = upstream.headers.get("content-type", "")

    # For HTML responses: rewrite absolute paths and inject fetch interceptor
    if "text/html" in content_type and content:
        proxy_prefix = f"/api/proxy/{job_id}"

        # Rewrite absolute paths in src="/" and href="/" attributes
        content = re.sub(
            rb'''(src|href|action)\s*=\s*["'](/(?!api/proxy/))''',
            lambda m: m.group(1) + b'="' + proxy_prefix.encode() + m.group(2),
            content,
        )

        # Inject script to intercept fetch() and XMLHttpRequest for absolute paths
        interceptor = f"""
<script>
(function() {{
  const PROXY = "{proxy_prefix}";

  // Override fetch to rewrite absolute paths
  const origFetch = window.fetch;
  window.fetch = function(url, opts) {{
    if (typeof url === 'string' && url.startsWith('/') && !url.startsWith(PROXY)) {{
      url = PROXY + url;
    }}
    return origFetch.call(this, url, opts);
  }};

  // Override XMLHttpRequest.open to rewrite absolute paths
  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url, ...args) {{
    if (typeof url === 'string' && url.startsWith('/') && !url.startsWith(PROXY)) {{
      url = PROXY + url;
    }}
    return origOpen.call(this, method, url, ...args);
  }};
}})();
</script>
""".encode()

        if b"<head>" in content:
            content = content.replace(b"<head>", b"<head>" + interceptor, 1)
        elif b"<HEAD>" in content:
            content = content.replace(b"<HEAD>", b"<HEAD>" + interceptor, 1)
        else:
            content = interceptor + content

    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=content_type or None,
    )
