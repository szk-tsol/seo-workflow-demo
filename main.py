from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.integrations.slack.security import verify_slack_signature
from app.integrations.slack.handlers import handle_slack_actions, handle_slack_events
from app.services import Services
from app.utils.logger import init_logging, get_logger
from urllib.parse import parse_qs

load_dotenv()
init_logging()
logger = get_logger(__name__)

settings = get_settings()

def get_services() -> Services:
    return Services(settings)


app = FastAPI(title="seo-workflow")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"ok": True}


@app.get("/")
async def root() -> Dict[str, Any]:
    return {"message": "seo-workflow"}


@app.post("/slack/events")
async def slack_events(request: Request):
    body = await request.body()
    _verify_slack_request(request, body)

    payload = await request.json()

    if payload.get("type") == "url_verification":
        return PlainTextResponse(str(payload.get("challenge") or ""), status_code=200)

    # Ack immediately; process async
    event = payload.get("event", {})
    if event.get("type") == "message" and event.get("thread_ts"):
        asyncio.create_task(
            get_services().process_slack_thread_message(
                thread_ts=event["thread_ts"],
                text=event.get("text", "")
            )
        )
    return JSONResponse({"ok": True})

@app.post("/slack/actions")
async def slack_actions(request: Request):
    body = await request.body()
    _verify_slack_request(request, body)

    # Slack は application/x-www-form-urlencoded
    decoded = body.decode("utf-8")
    parsed = parse_qs(decoded)

    payload_raw = parsed.get("payload", [None])[0]
    if not payload_raw:
        raise HTTPException(status_code=400, detail="missing payload")

    payload = json.loads(payload_raw)

    action = payload["actions"][0]

    normalized_action = {
        "action_id": action.get("action_id"),
        "value": action.get("value"),
        "channel_id": payload.get("channel", {}).get("id"),
        "message_ts": payload.get("message", {}).get("ts"),
    }

    asyncio.create_task(
        get_services().process_slack_action(normalized_action)
    )

    return JSONResponse({"ok": True})


@app.post("/jobs/notify_planned")
async def notify_planned(request: Request):
    _verify_jobs_token(request)

    # Ack can wait; this endpoint is called by scheduler
    res = await get_services().notify_planned()
    return JSONResponse(res)


def _verify_jobs_token(request: Request) -> None:
    token = request.headers.get("X-Jobs-Token", "")
    if not token or token != settings.jobs_token:
        raise HTTPException(status_code=401, detail="unauthorized")


def _verify_slack_request(request: Request, body: bytes) -> None:
    if request.headers.get("X-Slack-Retry-Num"):
        raise HTTPException(status_code=200, detail="retry ignored")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    try:
        verify_slack_signature(
            signing_secret=settings.slack_signing_secret,
            timestamp=timestamp,
            signature=signature,
            body=body,
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"invalid slack signature: {e}")
