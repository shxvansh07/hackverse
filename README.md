# Multilingual AI Clinical Assistant

A multilingual bridge between a patient and a doctor. Patients describe symptoms in
their own language; the assistant collects a structured clinical history; a
**deterministic** safety layer classifies the case; and a doctor makes every clinical
decision.

**The AI drafts. The doctor decides.**

---

## The safety model

This is not an autonomous diagnosis or prescribing system, and the architecture is
built so that it cannot drift into becoming one.

| Rule | Where it is enforced |
|---|---|
| URGENT cases never enter the prescription workflow | `safety/guards.py::may_generate_draft` |
| UNCERTAIN cases never bypass doctor review | `safety/guards.py::may_generate_draft` |
| No AI draft ever reaches a patient | `safety/guards.py::may_release_to_patient`, enforced at the API boundary |
| A rejected draft is never shown as a prescription | same guard, `REJECTED_BY_DOCTOR` |
| Translation cannot alter clinical content | `safety/guards.py::verify_translation` + `verify_medication_preserved` |
| A drafted drug never collides with a documented allergy | `safety/guards.py::check_allergy_conflict` |
| Symptoms, duration and history are never invented | additive-only merge in `services/triage_service.py` |
| A patient's own "this is severe" still forces doctor review, even with no matching red-flag phrase | `safety/engine.py::assess`, step 6 |
| Repeated failed doctor logins lock out the account for a cooldown window | `shared/auth.py::_is_locked_out` |

### The LLM has exactly one influence on routing, and it is one-directional

The model may flag a possible red flag it noticed. That hint can **raise** concern
(pushing a case to UNCERTAIN) but there is no code path by which any model output
marks a case safe or clears an escalation. Triage state is set only by
`safety/engine.py`, which imports no AI code and makes no network calls.

```
patient message
      ↓
LLM structured extraction  ── validated against a Pydantic schema; invalid output discarded
      ↓
additive merge into case state  ── a later turn can never blank an earlier disclosure
      ↓
DETERMINISTIC safety assessment  ── sole authority on LOW_RISK / UNCERTAIN / URGENT
      ↓
     ┌──────────────┬──────────────────┬─────────────────┐
   URGENT        UNCERTAIN          LOW_RISK
   escalate,     doctor review,     RAG-grounded draft
   no draft      no draft           → allergy check → doctor
```

### Where medications actually come from

Drugs, doses, frequencies and durations are copied **verbatim** from
`knowledge/formulary.json`. The LLM writes the rationale prose and nothing else. A
model cannot introduce a medication a human did not put in that file. If no protocol
matches, the draft is deliberately empty and the doctor prescribes from scratch —
**except** that a broader statistical classifier (`patient_backend/ml_predictor.py`,
trained on a real public 41-condition symptom dataset, well beyond the 6 conditions the
curated formulary covers) may add a diagnostic *hypothesis* — a condition name,
confidence and description — as reference text for the doctor. It is never permitted to
add, substitute or dose a medication; that boundary is enforced in `rag/engine.py`, not
merely by convention, and is covered by `tests/test_ml_predictor.py`.

---

## Running it

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add your NVIDIA NIM key
uvicorn app.main:app --reload --port 8000
```

Open <http://127.0.0.1:8000/docs> for the API, or `/api/health` to confirm which LLM
providers and knowledge files loaded.

**It runs with no API keys at all.** Intake falls back to a deterministic multilingual
question bank, and the safety engine is unaffected — which is also how the test suite
runs.

### Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:3000
```

### Tests

```bash
cd backend
PERSIST_STATE=0 SEED_DEMO_DATA=0 pytest -q      # 67 tests
```

`tests/test_safety.py` asserts the invariants above directly.
`tests/test_workflow.py` drives them through the real HTTP API.

---

## Demo script

1. Open `/patient`, choose **हिन्दी**.
2. Type or speak: `Mujhe 3 din se fever hai aur body pain bhi hai`.
3. The assistant replies in Hindi and asks only for what is still missing. The
   "Recorded so far" panel fills in as it learns.
4. Answer the allergy question, then say `nahi` — intake completes and hands off.
5. In another window, `/doctor` → sign in → the case **appears without a refresh**.
6. Review the English summary, safety signals, and the draft under its
   **AI-GENERATED DRAFT** banner.
7. **Modify** a dose and release, or **Approve** as-is.
8. Back on the patient window, the prescription appears. Switch language — the
   medicine name, dose and duration are byte-identical; only the surrounding words
   change.

To demonstrate escalation, start a fresh session and say
`I have severe chest pain and cannot breathe`. The case is classified URGENT, the
patient is told to seek emergency care, and **no prescription is generated** — the
doctor queue shows it top of the list with "No draft".

---

## Architecture

```
knowledge/                  Curated clinical data, reviewable without reading code
  formulary.json            Drugs, doses, contraindications per protocol
  red_flags.json            THE authority on URGENT classification
  clinical_guidance.json    Retrievable grounding passages
  icd10.json                Code reference

backend/app/
  ai/                       Provider abstraction. NVIDIA NIM primary, 4 fallbacks
    base.py                 LLMProvider interface
    providers.py            Vendor adapters
    schemas.py              Pydantic schemas for every structured model output
    prompts.py              Prompt construction
    service.py              Fallback chain + validation
  safety/                   Deterministic. Imports no AI code.
    engine.py               Triage classification
    guards.py               The four enforcement points
  rag/
    vector_store.py         Hand-rolled TF-IDF + cosine, zero dependencies
    engine.py               Retrieval and draft assembly
  services/                 Orchestration
  websocket/manager.py      Doctor queue fan-out
  patient_backend/router.py Patient endpoints
  doctor_backend/router.py  Doctor endpoints (all authenticated)
  shared/                   Models, store, auth, languages, knowledge loader

frontend/src/
  app/patient/              Mobile-first intake, speech I/O, prescription view
  app/doctor/               Desktop-first queue and review
  components/ui/clinical    Shared primitives (RiskBadge, AiDraftBanner…)
  lib/api.ts                Typed client
  lib/speech.ts             Web Speech API wrapper
```

### Why no vector database

The corpus is a few dozen curated passages. TF-IDF with cosine similarity in ~120
lines of pure Python is numerically identical to what a library would produce at this
size, with no dependency or infrastructure cost. `VectorStore` exposes `add`/`search`,
so swapping in pgvector later is a change behind one file.

### Why JSON persistence rather than an ORM

Four entity types, no query requirements beyond get-by-id and list-all. A dict plus an
atomic snapshot gives restart durability without a schema or migrations. All writes go
through `ClinicalStore`, so moving to SQLAlchemy is contained to that file. Doctor
decisions additionally append to an immutable JSONL audit log.

---

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/languages` | Language menu with Web Speech tags |
| POST | `/api/patient/session` | Start intake |
| POST | `/api/triage/message` | Send a message, get reply + updated state |
| POST | `/api/triage/assess` | Final safety assessment; drafts only if permitted |
| GET | `/api/triage/{session_id}` | Current structured state |
| POST | `/api/cases` | Hand off to the doctor queue |
| GET | `/api/patient/status/{session_id}` | Polled by the waiting screen |
| GET | `/api/prescriptions/{id}?lang=` | **Release gate.** 409 unless doctor-finalised |
| POST | `/api/auth/doctor/login` | |
| WS | `/api/ws/doctor?token=` | Live queue |
| GET | `/api/doctor/cases` | Queue, URGENT first |
| GET | `/api/doctor/cases/{id}` | Full detail incl. draft and audit |
| POST | `/api/doctor/cases/{id}/decision` | APPROVE / MODIFY / REJECT / NEEDS_REVIEW |
| PATCH | `/api/prescriptions/{id}` | Amend a finalised prescription |
| GET | `/api/doctor/audit/{case_id}` | Decision trail |

All doctor routes require `Authorization: Bearer <token>`.

---

## Languages

English and Hindi are the guaranteed MVP pair. Bengali, Marathi, Tamil, Telugu,
Gujarati and Kannada run through the identical code path. Adding another is one entry
in `shared/languages.py` plus its column in the fallback question bank — no engine
changes.

---

## Known limitations

- **Authentication is demo-grade.** Env-var credentials and in-memory tokens, now with
  brute-force lockout (`shared/auth.py`). Replace with a real identity provider before
  any real use — this is still not password hashing or a user store.
- **Red-flag matching is lexical.** Phrase matching plus a bounded-proximity token
  match. It errs toward over-detection on purpose, but it is not semantic and will
  miss unusual phrasings.
- **The formulary is illustrative**, curated for demonstration and not clinically
  validated. Six protocols covering common self-limiting presentations, backed by a
  41-condition statistical classifier for diagnostic *reasoning breadth* — see
  "Where medications actually come from" above. Neither is a substitute for a real
  clinical knowledge base.
- **Translation is verified, not guaranteed.** Numeric drift is caught and falls back
  to English; nuance loss within unchanged numbers would not be.
- **`npm audit` flags known Next.js 14.2.x CVEs** (DoS/SSRF/cache-poisoning classes in
  Server Actions, Middleware and the Image Optimizer). The fix is a major-version bump
  to Next 16, which has real breaking changes — not done here without dedicated
  regression testing across every page. Treat this as an open item before a public
  deployment, not as done.

## Not built, by design

Autonomous diagnosis or prescribing · doctor replacement · emergency response · EMR ·
FHIR/HL7 · microservices.

> Prototype for demonstration. Not a medical device and not for clinical use.
