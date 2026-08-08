# Multilingual AI Clinical Assistant — Team AUFBRUCH

HackVerse 2.0 2026. A two-portal system: a patient describes symptoms by **text or voice in one of 8 Indian languages**, an AI conversational engine triages them, a **deterministic safety layer** (not the LLM) decides how urgent the case is, and a **doctor dashboard** — synced in real time over WebSocket — reviews, edits, and approves everything before a patient ever sees a prescription.

## What this actually does

**Patient side (`/patient`)**
- Picks a language (Hindi, Kannada, Tamil, Telugu, Bengali, Marathi, Gujarati, or English) and describes symptoms — by typing, or hands-free via a full **voice call mode**: continuous speech recognition, silence-based auto-submit (Web Audio volume analysis, no push-to-talk), spoken AI replies, and a live equalizer while it listens.
- The AI (`ai_service.py`) asks natural follow-up questions in-language, refusing anything off-topic ("only health questions"), and never re-asks for information already given.
- Structured fields — symptoms, duration, severity, history, allergies — are extracted per turn (`triage_engine.py`), in English and Hindi/Hinglish, including Devanagari digits and 5 duration-phrase patterns.
- A **deterministic** safety engine (`safety_engine.py`) — not the LLM — scans for 8 categories of red flags (chest pain, respiratory distress, stroke signs, infant high fever, loss of consciousness, severe bleeding, anaphylaxis, severe abdominal pain) in English *and* Hindi/Hinglish, and classifies the case `LOW_RISK` / `UNCERTAIN` / `URGENT`. This runs independent of the LLM's own judgment on purpose — a bad LLM generation can't silently downgrade an emergency.
- `URGENT` → an emergency appointment is **auto-booked** immediately, in-language, no doctor action needed to reserve the slot.
- Otherwise, once intake is complete (or the patient says "no"/"nahi" to more questions), a **RAG-grounded draft prescription** is generated (`rag_engine.py`) from a small curated formulary (fever, cold, acidity, headache, diarrhea — each with ICD-10 code, medications, and care instructions) matched by keyword relevance.
- The patient then polls for the doctor's decision; once `APPROVED`/`MODIFIED`, the **canonical, doctor-approved** prescription renders — patient can switch the display language (`translation.py` translates frequency/instructions phrasing per language while **preserving medication name, dosage, and duration exactly** — translation never touches the clinical content).

**Doctor side (`/doctor`, gated behind `/doctor/login`)**
- Demo login (`doctor` / `doctorpassword123`, shown right on the form — see *Known limitations*).
- Case queue with live metrics (total / low-risk / urgent / pending), filterable by risk level, updated **in real time via WebSocket** the moment a patient submits — no polling, no manual refresh needed.
- Case detail: English clinical summary, red flags, editable AI-drafted medication rows (add/remove/edit inline).
- Five decisions, not three: **Approve**, **Modify & Approve**, **Reject**, plus two the base PRD didn't have — **Refer to Specialist** (8 specialties) and **Schedule In-Person Appointment** (clinic + time slot) — both generate their own record type (`ReferralInfo` / `Appointment`) attached to the case.

## Architecture

```
                         PATIENT                                    DOCTOR
                            │                                          │
                            ▼                                          ▼
                 Next.js Patient Portal                     Next.js Doctor Portal
              (voice call, 8-language chat)                (login, live queue, review)
                            │                                          │
                            │ REST                          REST + WebSocket (live push)
                            ▼                                          ▼
                 ┌────────────────────┐                    ┌────────────────────┐
                 │ routers/patient.py │                    │  routers/doctor.py │
                 └──────────┬─────────┘                    └──────────┬─────────┘
                            │                                          │
              ┌─────────────┼─────────────┐                            │
              ▼             ▼             ▼                            │
      triage_engine   safety_engine  ai_service                        │
      (structured      (deterministic  (Gemini→Groq→OpenAI→            │
       extraction)      red flags)      DeepSeek→NVIDIA→rules)         │
              │             │                                          │
              └──────┬──────┘                                          │
                     ▼                                                 │
              rag_engine.py  ◄─────────────────────────────────────────┘
           (formulary-grounded draft,          (also called if a doctor opens
            called on intake completion)        a case with no draft yet)
                     │                                                 │
                     ▼                                                 ▼
         ┌─────────────────────────────────────────────────────────────┐
         │              database.py — shared in-memory store            │
         │   sessions · cases · prescriptions · appointments             │
         │              + ConnectionManager (WebSocket broadcast)        │
         └─────────────────────────────────────────────────────────────┘
                     │
                     ▼
             translation.py
     (patient-language prescription view — never mutates clinical content)
```

`main.py` is a thin app factory that just wires the two routers together — both share the *same* in-memory `db` and `ws_manager` singletons in `database.py`, which is exactly how a doctor's decision instantly reaches the patient's next poll and the doctor queue's WebSocket push.

## Tech stack (as actually used, not aspirational)

| Layer | Technology |
|---|---|
| Patient + Doctor Web App | Next.js 14 (App Router) · TypeScript · Tailwind CSS · Framer Motion · Lucide icons |
| API | FastAPI · Pydantic v2 · native `WebSocket` support |
| AI conversation | Cascading fallback: Gemini → Groq (Llama 3.3 70B) → OpenAI (gpt-4o-mini) → DeepSeek → NVIDIA NIM → rule-based per-language question bank (works with **zero** API keys configured) |
| Safety triage | Deterministic keyword/regex engine, independent of the LLM |
| RAG | Curated in-code clinical formulary, keyword-relevance matched (no vector DB — appropriately simple for a 36h MVP) |
| Data store | In-memory (`InMemoryDB`) — resets on backend restart, seeded with 2 demo cases on boot |
| Real-time sync | Native WebSocket broadcast (`/api/ws/doctor`) |
| Voice | Browser-native Web Speech API (`SpeechRecognition` + `SpeechSynthesis`) + Web Audio API for voice-activity detection — no external speech service |

## Repo layout & 4-way ownership

```
backend/
  app/
    main.py            — thin app factory, wires both routers
    routers/
      patient.py        ← Patient Backend
      doctor.py          ← Doctor Backend
    models.py            (shared — Pydantic schemas both sides depend on)
    database.py           (shared — in-memory store + WebSocket manager)
    ai_service.py        ← Patient Backend  (LLM cascade)
    triage_engine.py     ← Patient Backend  (structured extraction)
    safety_engine.py     ← Patient Backend  (red-flag rules)
    rag_engine.py        ← Patient Backend  (formulary/draft — also called from doctor.py)
    translation.py       ← Patient Backend  (prescription language view)
  tests/test_backend.py  (safety, RAG, translation unit tests — run with `python -m unittest`)

frontend/
  src/app/
    page.tsx              (landing — links to both portals)
    patient/page.tsx     ← Patient Frontend  (chat + voice call + prescription view)
    doctor/login/page.tsx ← Doctor Frontend
    doctor/page.tsx       ← Doctor Frontend  (queue + case review + decisions)
  src/lib/api.ts          (shared typed client both portals import)
```

| Part | Owns | Consumes from shared/other side |
|---|---|---|
| **Patient Frontend** | `frontend/src/app/patient/page.tsx`, landing page | `lib/api.ts` |
| **Patient Backend** | `routers/patient.py`, `ai_service.py`, `triage_engine.py`, `safety_engine.py`, `rag_engine.py`, `translation.py` | `models.py`, `database.py` |
| **Doctor Frontend** | `frontend/src/app/doctor/page.tsx`, `frontend/src/app/doctor/login/page.tsx` | `lib/api.ts` |
| **Doctor Backend** | `routers/doctor.py` (auth, queue, decisions, WebSocket) | `models.py`, `database.py`, `rag_engine.py` (lazy-draft fallback) |

`models.py` and `database.py` are intentionally shared, not duplicated — both portals operate on the same `TriageCase`/`Prescription` records and the same in-memory store. Splitting those would mean two copies of the same data going out of sync.

## Setup & run

**Backend**
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add at least one LLM key, or leave blank to use the rule-based fallback
uvicorn app.main:app --reload --port 8000
```
Run tests: `python -m unittest tests.test_backend -v`

**Frontend**
```bash
cd frontend
npm install
npm run dev   # http://localhost:3000
```
Set `NEXT_PUBLIC_API_URL` (defaults to `http://127.0.0.1:8000`) if the backend runs elsewhere.

**Try it**
- Patient: http://localhost:3000/patient
- Doctor: http://localhost:3000/doctor/login → `doctor` / `doctorpassword123`

## Known limitations (hackathon MVP, by design)

- **No persistence** — the in-memory store resets every backend restart. Two demo cases (`CASE-DEMO-01` low-risk, `CASE-DEMO-02` urgent) are re-seeded on boot so the doctor queue is never empty.
- **Demo-only doctor auth** — hardcoded username/password shown directly on the login form, no hashing, wide-open `CORS allow_origins=["*"]`. Fine for a judged demo on localhost; would need real auth, a real DB, and locked-down CORS before any real deployment.
- **LLM keys optional but recommended** — without at least one of `GEMINI_API_KEY` / `GROQ_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` / `NVIDIA_API_KEY` in `.env`, conversation falls back to a fixed per-language question sequence (still fully functional, just not adaptive).
- **RAG formulary is intentionally small** (5 conditions) — a real deployment needs a reviewed, larger clinical knowledge base.
