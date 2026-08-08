# Multilingual AI Clinical Assistant — Team AUFBRUCH

HackVerse 2.0 2026. A two-portal system: a patient describes symptoms by **text or voice in one of 8 Indian languages**, an AI conversational engine triages them, a **deterministic safety layer** (not the LLM) decides how urgent the case is, and a **doctor dashboard** — synced in real time over WebSocket — reviews, edits, and approves everything before a patient ever sees a prescription.

## What this actually does

**Patient side (`/patient`)**
- Picks a language (Hindi, Kannada, Tamil, Telugu, Bengali, Marathi, Gujarati, or English) and describes symptoms — by typing, or hands-free via a full **voice call mode**: continuous speech recognition, silence-based auto-submit (Web Audio volume analysis, no push-to-talk), spoken AI replies, and a live equalizer while it listens.
- The AI (`ai_service.py`) asks natural follow-up questions in-language, refusing anything off-topic ("only health questions"), and never re-asks for information already given.
- Structured fields — symptoms, duration, severity, history, allergies — are extracted per turn (`triage_engine.py`), in English and Hindi/Hinglish, including Devanagari digits and 5 duration-phrase patterns.
- A **deterministic** safety engine (`safety_engine.py`) — not the LLM — scans for 8 categories of red flags (chest pain, respiratory distress, stroke signs, infant high fever, loss of consciousness, severe bleeding, anaphylaxis, severe abdominal pain) in English *and* Hindi/Hinglish, and classifies the case `LOW_RISK` / `UNCERTAIN` / `URGENT`. This runs independent of the LLM's own judgment on purpose — a bad LLM generation can't silently downgrade an emergency.
- `URGENT` (red flag detected) → an emergency appointment is **auto-booked** immediately, in-language, no doctor action needed to reserve the slot. No prescription is drafted for this case.
- Otherwise, severity decides what happens next — see *Severity-based routing* below. **MILD/MODERATE** → once intake is complete (or the patient says "no"/"nahi" to more questions), a **RAG-grounded draft prescription** is generated (`rag_engine.py`) from a small curated formulary (fever, cold, acidity, headache, diarrhea — each with ICD-10 code, medications, and care instructions) matched by keyword relevance — plus a **real ML classifier** (`ml_predictor.py`, trained on a public Kaggle-origin dataset) that adds a broader diagnostic hypothesis across 41 conditions when the curated formulary has no match. See *ML condition classifier* below for why this is architected as "predict the condition, never the dosage." **SEVERE** (patient describes it as severe themselves, even without a matched red-flag phrase) → no draft at all; patient is told directly to book an in-person appointment.
- The patient then polls for the doctor's decision; once `APPROVED`/`MODIFIED`, the **canonical, doctor-approved** prescription renders — patient can switch the display language (`translation.py` translates frequency/instructions phrasing per language while **preserving medication name, dosage, and duration exactly** — translation never touches the clinical content).

**Doctor side (`/doctor`, gated behind `/doctor/login`)**
- Demo login (`doctor` / `doctorpassword123`, shown right on the form — see *Known limitations*).
- Case queue with live metrics (total / low-risk / urgent / pending), filterable by risk level, updated **in real time via WebSocket** the moment a patient submits — no polling, no manual refresh needed. Each row shows a MILD/MODERATE/SEVERE severity badge alongside the risk state.
- Case detail: English clinical summary, red flags, severity badge, editable AI-drafted medication rows (add/remove/edit inline). Cases at MODERATE severity show an explicit hint next to the in-person appointment option — moderate can still become severe, so the doctor is prompted to consider booking one even while approving the draft.
- Five decisions, not three: **Approve**, **Modify & Approve**, **Reject**, plus two the base PRD didn't have — **Refer to Specialist** (8 specialties) and **Schedule In-Person Appointment** (clinic + time slot) — both generate their own record type (`ReferralInfo` / `Appointment`) attached to the case. The in-person option works even on a SEVERE case with no draft (nothing here requires a prescription to exist first).

## ML condition classifier (`patient_backend/ml_predictor.py`)

The original RAG formulary only covered 5 conditions. Rather than fabricate more entries, this pulls in a **real public dataset** and trains a **real classifier** on it — with two mistakes found and fixed along the way that are worth knowing about if you extend this:

- **Dataset**: "Disease Prediction Using Machine Learning" (Kaggle-origin, mirrored at [sohamvsonar/Disease-Prediction-and-Medical-Recommendation-System](https://github.com/sohamvsonar/Disease-Prediction-and-Medical-Recommendation-System)) — 4,920 rows, exactly 120 per class, 41 diseases, 132 binary symptom features. Lives in `patient_backend/data/`. It's a clean, balanced *teaching* dataset, not real de-identified EHR data — don't read high accuracy on it as clinical validation.
- **Mistake #1 — model choice**: a Decision Tree and then a Naive Bayes classifier were tried first. Naive Bayes predicted **"AIDS" at 96% confidence** for the input "fever + body ache" alone — an artifact of AIDS having an unusually sparse symptom footprint in this dataset, not a real signal. Switched to a **RandomForest** (200 trees), which gives sane, appropriately modest top guesses for the same input (hepatitis A 30% / Malaria 21% / Dengue 14% — all genuinely plausible, none falsely overconfident).
- **Mistake #2 — bad third-party data**: the same GitHub mirror ships a `medications.csv` mapping disease → drug list. Cross-checking it against `description.csv`/`precautions_df.csv` (which line up correctly) found scrambled rows — **"Heart attack" was mapped to varicose-vein treatments** (compression stockings, sclerotherapy) and **"Varicose veins" was mapped to thyroid medications** (Levothyroxine, radioactive iodine). That file (and the unused `diets.csv`) was deleted from this repo entirely — only `Training.csv`, `description.csv`, and `precautions_df.csv` are used, all spot-checked for correct alignment.
- **The safety boundary this leaves**: `ml_predictor.predict_condition()` returns a condition name + confidence + description + precautions — never a medication or dosage. In `rag_engine.py`, a predicted condition only becomes a concrete medication line if it's *also* in the small, manually-verified `DOSAGE_REFERENCE`/`CLINICAL_KNOWLEDGE_BASE`; otherwise the draft explicitly reads "no verified formulary match — doctor to determine treatment" instead of inventing a dose. Predicting a diagnosis from a real dataset is a legitimate, well-scoped ML use; predicting a dosage from a public dataset (this one or any other) is not something to fake at scale — see `ml_predictor.py`'s module docstring for the full reasoning.
- Requires `pandas` + `scikit-learn` (in `requirements.txt`); trains in-process at import time (<1s on 4,920 rows — no separate training step or model file to keep in sync).

## Severity-based routing (MILD / MODERATE / SEVERE)

A second classification, deliberately kept separate from `RiskState` (`LOW_RISK`/`UNCERTAIN`/`URGENT`, the red-flag routing decision above). `SeverityLevel` (`shared/models.py`) drives what happens to the case next; `classify_severity()` (`safety_engine.py`) computes it deterministically, same as the red-flag rules:

| Severity | Triggered by | What happens |
|---|---|---|
| **SEVERE** | A red flag matched (`RiskState.URGENT`) **or** the patient described their *own* symptoms as "severe"/"gambhir"/"बहुत तेज", even with no matching red-flag phrase | **No prescription is drafted.** Red-flag case: emergency appointment auto-booked immediately (existing behavior). Self-reported-only case: patient is told directly to book an in-person appointment — not auto-booked, since it didn't trip the specific red-flag list, but still routed to a doctor visit instead of a home prescription. |
| **MODERATE** | Self-reported "moderate", a `RiskState.UNCERTAIN` result (safety engine wasn't confident enough to call it low-risk), or 3+ reported symptoms | Prescription **is** drafted and sent to the doctor for review/approval, same as MILD — but the doctor dashboard shows an explicit precaution hint next to the in-person appointment option, since a moderate case can still turn severe. |
| **MILD** | Everything else | Prescription drafted and sent to the doctor for review/approval, as originally designed. |

Why this exists as a second axis instead of overloading `RiskState`: `RiskState` is specifically the deterministic red-flag safety net (a fixed list of dangerous phrases) — expanding what counts as "urgent" there risks diluting a list that's supposed to be a small, high-precision set of genuine emergencies. `SeverityLevel` handles the softer, more common case — a patient who isn't describing an emergency phrase but is still telling you, in their own words, that this feels serious — without touching that list. The self-report path only ever *skips a prescription draft and suggests booking*; it never auto-books or triggers emergency-room language, since it hasn't been verified against the red-flag patterns the way a true `URGENT` case has.

**Specialist recommendation** (`recommend_specialty()`, `safety_engine.py`) is layered on top of both SEVERE paths — a red flag maps to a specific specialty (chest pain → Cardiology, stroke signs → Neurology, etc.), falling back to "General Physician / Internal Medicine" when nothing maps cleanly (which is what the self-report-only path always gets, since it hasn't matched a specific red-flag category). Shown to the patient in both the emergency banner and the appointment-recommended card, and passed through to `book_appointment()` so the booked slot records which specialty it's for.

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
         │           shared/database.py — shared in-memory store         │
         │   sessions · cases · prescriptions · appointments             │
         │              + ConnectionManager (WebSocket broadcast)        │
         └─────────────────────────────────────────────────────────────┘
                     │
                     ▼
             translation.py
     (patient-language prescription view — never mutates clinical content)
```

`main.py` is a thin app factory that just wires `patient_backend.router` and `doctor_backend.router` together — both import the *same* in-memory `db` and `ws_manager` singletons from `shared/database.py`, which is exactly how a doctor's decision instantly reaches the patient's next poll and the doctor queue's WebSocket push. That in-memory store has to stay a single process — it's why this is one backend app with two router folders, not two separate backend services.

## Tech stack (as actually used, not aspirational)

| Layer | Technology |
|---|---|
| Patient + Doctor Web App | Next.js 14 (App Router) · TypeScript · Tailwind CSS · Framer Motion · Lucide icons |
| API | FastAPI · Pydantic v2 · native `WebSocket` support |
| AI conversation | Cascading fallback: Gemini → Groq (Llama 3.3 70B) → OpenAI (gpt-4o-mini) → DeepSeek → NVIDIA NIM → rule-based per-language question bank (works with **zero** API keys configured) |
| Safety triage | Deterministic keyword/regex engine, independent of the LLM |
| RAG | Curated in-code clinical formulary (verified dosages, 5 conditions), keyword-relevance matched, backed by a **RandomForestClassifier** (scikit-learn, trained on a real public 41-disease/4,920-row dataset) for broader diagnostic hypotheses — no vector DB, appropriately simple for a 36h MVP |
| Data store | In-memory (`InMemoryDB`) — resets on backend restart, seeded with 2 demo cases on boot |
| Real-time sync | Native WebSocket broadcast (`/api/ws/doctor`) |
| Voice | Browser-native Web Speech API (`SpeechRecognition` + `SpeechSynthesis`) + Web Audio API for voice-activity detection — no external speech service |

## Repo layout & 4-way ownership

Four folders, one for each part. `patient_backend/` and `doctor_backend/` use underscores, not hyphens — Python's `import` syntax doesn't allow hyphens in package names, so `patient-backend` isn't a valid importable folder name. The frontend route folders (`app/patient/`, `app/doctor/`) keep their existing names since they're also the live URL paths (`/patient`, `/doctor`) — renaming them would break every link and bookmark to the app.

```
backend/
  app/
    main.py                  — thin app factory: imports both routers, wires them onto one FastAPI app
    shared/                  ← used by both backend parts, not owned by either
      models.py               (Pydantic schemas — TriageCase, Prescription, etc.)
      database.py              (in-memory store + WebSocket ConnectionManager)
    patient_backend/         ← Patient Backend folder
      router.py                (session, triage chat, prescription GET, appointment booking)
      ai_service.py            (LLM cascade: Gemini → Groq → OpenAI → DeepSeek → NVIDIA → rules)
      triage_engine.py         (structured field extraction)
      safety_engine.py         (deterministic red-flag rules)
      rag_engine.py            (formulary-grounded draft — doctor_backend also calls this)
      ml_predictor.py          (RandomForest classifier — see "ML condition classifier" above)
      translation.py           (prescription language view)
      data/                    (Training.csv, description.csv, precautions_df.csv — dataset)
    doctor_backend/          ← Doctor Backend folder
      router.py                (auth, WebSocket, case queue, decisions)
  tests/test_backend.py       (safety, RAG, translation unit tests — run with `python -m unittest`)

frontend/
  src/app/
    page.tsx                   (landing — links to both portals)
    patient/page.tsx          ← Patient Frontend  (chat + voice call + prescription view)
    doctor/login/page.tsx     ← Doctor Frontend
    doctor/page.tsx            ← Doctor Frontend  (queue + case review + decisions)
  src/lib/api.ts               (shared typed client both portals import)
```

| Part | Folder | Consumes from `shared/` |
|---|---|---|
| **Patient Frontend** | `frontend/src/app/patient/` + landing page | `lib/api.ts` |
| **Patient Backend** | `backend/app/patient_backend/` | `shared/models.py`, `shared/database.py` |
| **Doctor Frontend** | `frontend/src/app/doctor/` | `lib/api.ts` |
| **Doctor Backend** | `backend/app/doctor_backend/` | `shared/models.py`, `shared/database.py`, `patient_backend/rag_engine.py` (lazy-draft fallback when a doctor opens a case with no draft yet) |

`shared/models.py` and `shared/database.py` are intentionally shared, not duplicated per folder — both backend parts operate on the same `TriageCase`/`Prescription` records and the same in-memory store (and the same process, since the WebSocket broadcast and in-memory DB only work as one running server). Duplicating them would mean two copies of the same data silently going out of sync. Each of the 4 folders above is otherwise fully independent — one person's PR only ever touches their own folder, so there's no file-level collision between the 4 of you.

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
- **Verified-dosage formulary is intentionally small** (5 conditions) — the ML classifier broadens diagnostic *reasoning* to 41 conditions, but a real deployment still needs a clinician-reviewed medication/dosage reference for all of them, not just 5. This is a deliberate safety choice, not an oversight — see *ML condition classifier* above.
- **The ML classifier is trained on a small, clean, synthetic-feeling teaching dataset** (4,920 rows, 41 diseases) — good enough to demo real ML with real data on a hackathon timeline, not a substitute for a clinically validated diagnostic model.
- **Self-reported severity detection is simple substring matching** ("severe"/"gambhir"/etc.), same technique the rest of `triage_engine.py` already uses — it won't catch every phrasing a real patient might use to signal something is serious. It's a genuine safety net on top of the red-flag list, not a replacement for one; a case that says nothing matching either still falls back to MILD/MODERATE and drafting.
