"""Application entry point."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

from app.ai.service import ai_service  # noqa: E402
from app.doctor_backend import router as doctor_router  # noqa: E402
from app.patient_backend import router as patient_router  # noqa: E402
from app.rag.engine import rag_engine  # noqa: E402
from app.shared import knowledge  # noqa: E402
from app.shared.database import db  # noqa: E402

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Build the RAG index at boot so the first patient does not pay for it,
    and surface a missing knowledge file immediately rather than mid-demo."""
    rag_engine.load()
    stats = knowledge.health()
    log = logging.getLogger(__name__)
    if not stats["red_flags"]:
        log.error(
            "No red flags loaded from %s — URGENT triage will not fire. "
            "Check the knowledge directory.",
            stats["knowledge_dir"],
        )
    log.info(
        "Ready. LLM providers: %s. Knowledge: %s",
        ", ".join(ai_service.provider_names) or "none configured (deterministic fallback)",
        stats,
    )
    yield


app = FastAPI(
    title="Multilingual AI Clinical Assistant",
    version="3.0.0",
    description=(
        "Multilingual patient intake with deterministic safety triage and "
        "doctor-authorised prescribing. The AI drafts; the doctor decides."
    ),
    lifespan=lifespan,
)

# Browser origins only. Credentials are off because auth travels as a bearer
# token, so a wildcard origin cannot be used to replay a cookie.
_origins = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(patient_router.router)
app.include_router(doctor_router.router)


@app.get("/")
def read_root():
    return {"status": "online", "service": "Multilingual AI Clinical Assistant", "version": "3.0.0"}


@app.get("/api/health")
def health():
    """Boot diagnostics. Reports provider and knowledge state without
    exposing any key material."""
    return {
        "status": "ok",
        "llm_providers": ai_service.provider_names,
        "llm_available": ai_service.available,
        "knowledge": knowledge.health(),
        "rag": rag_engine.health(),
        "cases": len(db.cases),
        "sessions": len(db.sessions),
        "prescriptions": len(db.prescriptions),
    }
