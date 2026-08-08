from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.doctor_backend import router as doctor_router
from app.patient_backend import router as patient_router

app = FastAPI(
    title="Clinical Assistant API",
    version="2.1.0",
    description="Multilingual Intake, AI Triage, Real-time Doctor Handoff, Referral & Offline Appointment Engine"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"status": "online", "version": "2.1.0"}


app.include_router(patient_router.router)
app.include_router(doctor_router.router)
