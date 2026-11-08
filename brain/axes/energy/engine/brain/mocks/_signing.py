"""Optional HMAC signing for the mocks: verify signed requests, sign every response."""
from __future__ import annotations

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

from energy_brain.adapters.signing import sign, verify


def add_signing(app: FastAPI, secret: str | None, skip_prefixes: tuple[str, ...] = ("/_", "/health")) -> None:
    if not secret:
        return

    @app.middleware("http")
    async def _sign(request: Request, call_next):
        if request.url.path.startswith(skip_prefixes):
            return await call_next(request)
        body = await request.body()
        if request.method == "POST":
            bad = verify(secret, body, request.headers.get("x-timestamp"), request.headers.get("x-signature"))
            if bad:
                return JSONResponse({"detail": f"request refused: {bad}"}, status_code=401)
        resp = await call_next(request)
        payload = b"".join([chunk async for chunk in resp.body_iterator])
        headers = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
        headers.update(sign(secret, payload))
        return Response(payload, status_code=resp.status_code, headers=headers, media_type=resp.media_type)
