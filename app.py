from __future__ import annotations

import asyncio
import os
from functools import lru_cache
from typing import Any

from botbuilder.core import ActivityHandler, BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.chat import ChatQueryEngine
from src.config import load_settings


class AnswerRequest(BaseModel):
    question: str = Field(..., min_length=1)
    user_id: str | None = None
    conversation_id: str | None = None


class AnswerResponse(BaseModel):
    answer: str
    citations: list[dict[str, Any]]
    trace_id: str
    profile: dict[str, Any]
    active_query: str
    retrieval_skipped: bool


@lru_cache(maxsize=1)
def get_chat_engine() -> ChatQueryEngine:
    settings = load_settings()
    return ChatQueryEngine(settings, enable_trace_log=False)


app = FastAPI(title="Legal RAG API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LegalRagBot(ActivityHandler):
    async def on_message_activity(self, turn_context: TurnContext) -> None:
        user_text = TurnContext.remove_recipient_mention(turn_context.activity)
        if not user_text:
            user_text = turn_context.activity.text or ""
        user_text = user_text.strip()

        if not user_text:
            await turn_context.send_activity("Ask a legal or regulatory question about the indexed corpus.")
            return

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, get_chat_engine().answer_once, user_text)
        await turn_context.send_activity(_format_teams_answer(result))


def _format_teams_answer(result: dict[str, Any]) -> str:
    lines = [result["answer"].strip()]

    citations = result.get("citations") or []
    if citations:
        lines.append("")
        lines.append("Sources:")
        for citation in citations:
            source_id = citation.get("source_id")
            doc_name = citation.get("doc_name") or "unknown"
            page_row = citation.get("page_row_num") or "NA"
            article = citation.get("article_num") or "NA"
            annex = citation.get("annex_num") or "NA"
            lines.append(f"[{source_id}] {doc_name}, page/row={page_row}, article={article}, annex={annex}")

    lines.append("")
    lines.append(f"Trace ID: {result['trace_id']}")
    return "\n".join(lines)


bot_adapter = BotFrameworkAdapter(
    BotFrameworkAdapterSettings(
        app_id=os.getenv("MicrosoftAppId", ""),
        app_password=os.getenv("MicrosoftAppPassword", ""),
        channel_auth_tenant=os.getenv("MicrosoftAppTenantId"),
    )
)
bot = LegalRagBot()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "legal-rag-api"}


@app.post("/rag/answer", response_model=AnswerResponse)
def answer(request: AnswerRequest) -> dict[str, Any]:
    try:
        return get_chat_engine().answer_once(request.question)
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"RAG answer failed: {ex}") from ex


@app.post("/api/messages")
async def messages(request: Request) -> Response:
    if "application/json" not in request.headers.get("content-type", ""):
        raise HTTPException(status_code=415, detail="Expected application/json")

    body = await request.json()
    activity = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")

    try:
        invoke_response = await bot_adapter.process_activity(activity, auth_header, bot.on_turn)
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"Bot message failed: {ex}") from ex

    if invoke_response:
        return JSONResponse(status_code=invoke_response.status, content=invoke_response.body)
    return Response(status_code=201)
