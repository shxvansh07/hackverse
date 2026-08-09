/**
 * Typed client for the clinical API.
 *
 * Mirrors the backend Pydantic models. Two deliberate properties:
 *
 *  - `Prescription` (the doctor's view, may be an unapproved draft) and
 *    `PresentedPrescription` (the patient's view, only ever doctor-finalised)
 *    are separate types. They are not interchangeable, and the type system
 *    should stop anyone rendering the first where the second belongs.
 *  - The API base URL is the only configuration here. No API keys ever reach
 *    the browser; every model call happens server-side.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

export type RiskState = 'LOW_RISK' | 'UNCERTAIN' | 'URGENT';

export type ReviewStatus =
  | 'NEW' | 'IN_REVIEW' | 'NEEDS_REVIEW'
  | 'APPROVED' | 'MODIFIED' | 'REJECTED' | 'URGENT'
  | 'REFERRED' | 'OFFLINE_SCHEDULED';

export type PatientStatus =
  | 'COLLECTING_INFORMATION' | 'ASSESSING' | 'WAITING_FOR_DOCTOR'
  | 'URGENT_ESCALATION' | 'APPROVED' | 'REVIEW_COMPLETE';

export type PrescriptionStatus =
  | 'DRAFT' | 'APPROVED' | 'MODIFIED' | 'REJECTED' | 'NEEDS_REVIEW';

export type DecisionType =
  | 'APPROVE' | 'MODIFY' | 'REJECT' | 'NEEDS_REVIEW'
  | 'REFERRAL' | 'OFFLINE_APPOINTMENT';

export type AppointmentType =
  | 'OPTIONAL_CONSULT' | 'URGENT_EMERGENCY' | 'DOCTOR_SCHEDULED_OFFLINE' | 'SPECIALIST_CONSULT';

export interface Language {
  code: string;
  english_name: string;
  native_name: string;
  /** BCP-47 tag for the Web Speech API. */
  speech_tag: string;
  mvp: boolean;
}

/** Letterhead identity for the printable prescription. Display-only. */
export interface ClinicInfo {
  hospital_name: string;
  hospital_address: string;
  hospital_phone: string;
  hospital_registration_no: string;
  doctor_name: string;
  doctor_qualification: string;
  doctor_registration_no: string;
}

export interface Medication {
  name: string;
  dosage: string;
  frequency: string;
  duration: string;
  instructions: string;
}

export interface ChatMessage {
  sender: 'patient' | 'ai' | 'system';
  text: string;
  timestamp: string;
}

export interface SafetySignal {
  risk_state: RiskState;
  red_flags: string[];
  red_flag_codes: string[];
  missing_information: string[];
  reasons: string[];
  eligible_for_draft: boolean;
  evaluated_at: string;
}

/** A doctor's manual referral to a specialist. Distinct from
 *  TriageCase.recommended_specialty, which is the deterministic engine's
 *  suggestion — this is the doctor's own decision. */
export interface ReferralInfo {
  specialty: string;
  referral_notes: string;
  doctor_name: string;
  created_at: string;
}

/** A booked visit. Always the result of an explicit confirmation — a patient
 *  clicking "confirm", or a doctor decision — never created automatically. */
export interface Appointment {
  appointment_id: string;
  case_id: string;
  patient_id: string;
  doctor_id: string;
  doctor_name: string;
  type: AppointmentType;
  slot_time: string;
  clinic_location: string;
  status: string;
  notes: string;
  specialty: string | null;
  created_at: string;
}

/** One utterance in a live face-to-face consultation, kept both as said and
 *  as translated so the transcript is legible in either language. */
export interface ConsultationTurn {
  speaker: 'doctor' | 'patient';
  original_text: string;
  original_lang: string;
  translated_text: string;
  translated_lang: string;
  timestamp: string;
}

/** An in-person visit, real-time-interpreted on one shared device. */
export interface LiveConsultation {
  consultation_id: string;
  case_id: string;
  doctor_id: string;
  doctor_name: string;
  patient_lang: string;
  status: 'IN_PROGRESS' | 'COMPLETED';
  turns: ConsultationTurn[];
  /** English-only internal note. Never translated or shown to the patient —
   *  it lives in the case record for the doctor (see CaseRecord). */
  report_en: string | null;
  started_at: string;
  ended_at: string | null;
}

export interface TriageCase {
  case_id: string;
  session_id: string;
  patient_id: string;
  patient_name?: string;
  preferred_language: string;
  chief_complaint: string;
  symptoms: string[];
  associated_symptoms: string[];
  duration: string;
  severity: string;
  medical_history: string[];
  medications: string[];
  allergies: string[];
  age: string;
  allergies_confirmed: boolean;
  history_confirmed: boolean;
  red_flags: string[];
  missing_information: string[];
  triage_status: RiskState;
  safety_signal: SafetySignal | null;
  recommended_specialty: string | null;
  review_status: ReviewStatus;
  summary_en: string;
  transcript: ChatMessage[];
  prescription_id: string | null;
  appointment_id: string | null;
  referral: ReferralInfo | null;
  consultation_id: string | null;
  grounding: Record<string, unknown>;
  handed_off: boolean;
  handed_off_at: string | null;
  case_notes: CaseNote[];
  created_at: string;
  updated_at: string;
}

/** Doctor-to-doctor note on a case — append-only, e.g. a senior doctor
 * flagging context for whoever reviews it next. */
export interface CaseNote {
  note_id: string;
  doctor_id: string;
  doctor_name: string;
  text: string;
  created_at: string;
}

/** Doctor-side view. May be an unapproved AI draft — check `is_ai_draft`. */
export interface Prescription {
  prescription_id: string;
  case_id: string;
  status: PrescriptionStatus;
  medications: Medication[];
  instructions: string;
  is_ai_draft: boolean;
  doctor_id: string | null;
  doctor_name: string | null;
  doctor_notes: string | null;
  approved_at: string | null;
  referral: ReferralInfo | null;
  icd10_code: string;
  icd10_title: string;
  matched_condition: string;
  rationale: string;
  grounding_sources: string[];
  created_at: string;
  updated_at: string;
}

export interface PresentedMedication {
  name: string;
  dosage: string;
  duration: string;
  frequency: string;
  instructions: string;
  frequency_localised: string;
  instructions_localised: string;
}

/** Patient-side view. The API only ever returns this for a finalised record. */
export interface PresentedPrescription {
  prescription_id: string;
  case_id: string;
  status: PrescriptionStatus;
  language: string;
  requested_language: string;
  translation_applied: boolean;
  translation_notice: string | null;
  medications: PresentedMedication[];
  instructions: string;
  instructions_localised: string;
  doctor_name: string | null;
  doctor_notes: string | null;
  approved_at: string | null;
  icd10_code: string;
  is_ai_draft: boolean;
}

export interface PatientProfile {
  patient_id: string;
  name: string;
  age: string;
  created_at: string;
}

export interface PatientHistoryItem {
  case_id: string;
  session_id: string;
  chief_complaint: string;
  triage_status: RiskState;
  timestamp: string;
  prescription_id: string | null;
  consultation_id: string | null;
  has_prescription: boolean;
  has_consultation: boolean;
}

export interface PatientSession {
  session_id: string;
  patient_id: string;
  patient_name: string;
  preferred_language: string;
  status: PatientStatus;
  created_at: string;
  updated_at: string;
}

export interface TriageResponse {
  session_id: string;
  case_id: string;
  ai_response: string;
  language: string;
  patient_status: PatientStatus;
  triage_status: RiskState;
  is_complete: boolean;
  missing_information: string[];
  red_flags: string[];
  urgent_guidance: string | null;
  /** True for URGENT or UNCERTAIN — tells the UI to offer booking instead of
   *  waiting for a prescription. Never implies anything is already booked. */
  recommend_appointment: boolean;
  recommended_specialty: string | null;
  clinical_state: TriageCase | null;
}

export interface AssessResponse {
  session_id: string;
  case_id: string;
  triage_status: RiskState;
  safety_signal: SafetySignal;
  summary_en: string;
  draft_generated: boolean;
  draft_blocked_reason: string | null;
  patient_status: PatientStatus;
  recommend_appointment: boolean;
  recommended_specialty: string | null;
}

export interface PatientStatusResponse {
  session_id: string;
  case_id: string | null;
  patient_status: PatientStatus;
  triage_status: RiskState;
  review_complete: boolean;
  prescription_available: boolean;
  prescription_id: string | null;
  rejected: boolean;
  message: string;
  recommend_appointment: boolean;
  recommended_specialty: string | null;
  appointment: Appointment | null;
}

/** The permanent record of one patient encounter — initial conversation,
 *  consultation transcript (if any), and the finalized prescription (if
 *  any). Doctor-facing only; never sent to the patient. Shaped for reuse by
 *  a future per-patient history feature: one of these per past case. */
export interface CaseRecord {
  case_id: string;
  patient_id: string;
  chief_complaint: string;
  created_at: string;
  finalized_at: string | null;
  review_status: ReviewStatus;
  initial_conversation: ChatMessage[];
  consultation_transcript: ConsultationTurn[];
  encounter_note: string | null;
  prescription: Prescription | null;
}

export interface PatientHistoryRecentCase {
  case_id: string;
  created_at: string;
  chief_complaint: string;
  triage_status: RiskState;
}

export interface PatientHistory {
  visit_count: number;
  recent_cases: PatientHistoryRecentCase[];
  carried_forward: boolean;
  highlight: string | null;
}

export interface CaseDetail {
  case: TriageCase;
  prescription_draft: Prescription | null;
  safety_signal: SafetySignal | null;
  grounding: Record<string, any>;
  audit: AuditEvent[];
  draft_blocked: boolean;
  draft_block_reason: string | null;
  appointment: Appointment | null;
  referral: ReferralInfo | null;
  consultation: LiveConsultation | null;
  patient_history: PatientHistory | null;
  patient_name?: string;
  patient_profile?: PatientProfile | null;
  record: CaseRecord;
}

export interface AuditEvent {
  event_id: string;
  case_id: string | null;
  prescription_id: string | null;
  actor: string;
  action: string;
  detail: string;
  metadata: Record<string, unknown>;
  timestamp: string;
}

export interface DecisionResponse {
  case_id: string;
  review_status: ReviewStatus;
  prescription: Prescription | null;
  released_to_patient: boolean;
  message: string;
}

/** Error carrying the HTTP status so callers can distinguish 409 from 500. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly reason?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

const TOKEN_KEY = 'clinical_doctor_token';
const NAME_KEY = 'clinical_doctor_name';

function readToken(): string | null {
  if (typeof window === 'undefined') return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

async function request<T>(path: string, init: RequestInit = {}, auth = false): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((init.headers as Record<string, string>) || {}),
  };

  if (auth) {
    const token = readToken();
    if (!token) throw new ApiError('Not signed in', 401, 'NO_TOKEN');
    headers.Authorization = `Bearer ${token}`;
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch {
    throw new ApiError('Cannot reach the clinical service. Check your connection.', 0, 'NETWORK');
  }

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    let reason: string | undefined;
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') {
        message = body.detail;
      } else if (body.detail && typeof body.detail === 'object') {
        message = body.detail.message || message;
        reason = body.detail.reason;
      }
    } catch {
      /* non-JSON error body; keep the generic message */
    }

    if (response.status === 401 && auth && typeof window !== 'undefined') {
      window.localStorage.removeItem(TOKEN_KEY);
      window.localStorage.removeItem(NAME_KEY);
    }
    throw new ApiError(message, response.status, reason);
  }

  if (response.status === 204) return undefined as T;
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError('Received invalid response from clinical service.', response.status, 'INVALID_RESPONSE');
  }
}

export const api = {
  // ---------------------------------------------------------------- patient

  listLanguages: () =>
    request<{ languages: Language[] }>('/api/languages').then((r) => r.languages),

  getClinicInfo: () => request<ClinicInfo>('/api/clinic-info'),

  createProfile: (name: string, age: string) =>
    request<PatientProfile>('/api/patient/profile', {
      method: 'POST',
      body: JSON.stringify({ name, age }),
    }),

  getPatientHistory: (patientId: string) =>
    request<{ history: PatientHistoryItem[] }>(`/api/patient/${patientId}/history`).then(
      (r) => r.history
    ),

  startSession: (language: string, patientName = 'Patient', phone?: string, patientId?: string) =>
    request<PatientSession>('/api/patient/session', {
      method: 'POST',
      body: JSON.stringify({
        preferred_language: language,
        patient_name: patientName,
        phone: phone || undefined,
        patient_id: patientId,
      }),
    }),

  sendMessage: (sessionId: string, message: string) =>
    request<TriageResponse>('/api/triage/message', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, message }),
    }),

  /** Current structured state. Never contains prescription content. */
  getTriageState: (sessionId: string) =>
    request<{
      session: PatientSession;
      case: TriageCase | null;
      triage_status: RiskState;
      missing_information: string[];
      recommend_appointment: boolean;
      recommended_specialty: string | null;
      appointment: Appointment | null;
    }>(`/api/triage/${sessionId}`),

  assess: (sessionId: string) =>
    request<AssessResponse>('/api/triage/assess', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId }),
    }),

  handOff: (sessionId: string) =>
    request<TriageCase>('/api/cases', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId }),
    }),

  patientStatus: (sessionId: string) =>
    request<PatientStatusResponse>(`/api/patient/status/${sessionId}`),

  /** Returns 409 for anything not doctor-finalised — that is the safety gate. */
  getPrescription: (prescriptionId: string, lang = 'en') =>
    request<PresentedPrescription>(`/api/prescriptions/${prescriptionId}?lang=${lang}`),

  /** Patient-confirmed booking. Never called automatically by the app —
   *  even URGENT requires this explicit call. */
  bookAppointment: (caseId: string, slotTime?: string, clinicLocation?: string, specialty?: string) =>
    request<Appointment>('/api/appointments/book', {
      method: 'POST',
      body: JSON.stringify({
        case_id: caseId,
        slot_time: slotTime ?? null,
        clinic_location: clinicLocation ?? null,
        specialty: specialty ?? null,
      }),
    }),

  // ----------------------------------------------------------------- doctor

  async doctorLogin(username: string, password: string) {
    const data = await request<{
      token: string; doctor_id: string; doctor_name: string; expires_at: string;
    }>('/api/auth/doctor/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    });
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(TOKEN_KEY, data.token);
      window.localStorage.setItem(NAME_KEY, data.doctor_name);
    }
    return data;
  },

  doctorLogout() {
    if (typeof window === 'undefined') return;
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(NAME_KEY);
  },

  isDoctorAuthenticated: () => Boolean(readToken()),
  doctorName: () =>
    typeof window === 'undefined' ? null : window.localStorage.getItem(NAME_KEY),
  doctorToken: readToken,

  getDoctorCases: (riskFilter?: string, statusFilter?: string) => {
    const params = new URLSearchParams();
    if (riskFilter) params.set('risk_filter', riskFilter);
    if (statusFilter) params.set('status_filter', statusFilter);
    const qs = params.toString();
    return request<TriageCase[]>(`/api/doctor/cases${qs ? `?${qs}` : ''}`, {}, true);
  },

  getCaseDetail: (caseId: string) =>
    request<CaseDetail>(`/api/doctor/cases/${caseId}`, {}, true),

  addCaseNote: (caseId: string, text: string) =>
    request<CaseNote>(
      `/api/doctor/cases/${caseId}/notes`,
      { method: 'POST', body: JSON.stringify({ text }) },
      true,
    ),

  submitDecision: (
    caseId: string,
    decision: DecisionType,
    options: {
      notes?: string;
      modifiedMedications?: Medication[];
      modifiedInstructions?: string;
      referralSpecialty?: string;
      referralNotes?: string;
      offlineAppointmentTime?: string;
      offlineClinicLocation?: string;
    } = {},
  ) =>
    request<DecisionResponse>(
      `/api/doctor/cases/${caseId}/decision`,
      {
        method: 'POST',
        body: JSON.stringify({
          decision,
          notes: options.notes ?? null,
          modified_medications: options.modifiedMedications ?? null,
          modified_instructions: options.modifiedInstructions ?? null,
          referral_specialty: options.referralSpecialty ?? null,
          referral_notes: options.referralNotes ?? null,
          offline_appointment_time: options.offlineAppointmentTime ?? null,
          offline_clinic_location: options.offlineClinicLocation ?? null,
        }),
      },
      true,
    ),

  amendPrescription: (
    prescriptionId: string,
    payload: { medications?: Medication[]; instructions?: string; doctor_notes?: string },
  ) =>
    request<Prescription>(
      `/api/prescriptions/${prescriptionId}`,
      { method: 'PATCH', body: JSON.stringify(payload) },
      true,
    ),

  // ---------------------------------------------------- live consultation

  /** Doctor-driven: single shared device, so all four calls sit under the
   *  doctor auth boundary even though the outcome (the report) reaches the
   *  patient too, via patientStatus() above. */
  startConsultation: (caseId: string) =>
    request<LiveConsultation>(
      '/api/doctor/consultations/start',
      { method: 'POST', body: JSON.stringify({ case_id: caseId }) },
      true,
    ),

  getConsultation: (consultationId: string) =>
    request<LiveConsultation>(`/api/doctor/consultations/${consultationId}`, {}, true),

  submitConsultationTurn: (
    consultationId: string,
    turn: { speaker: 'doctor' | 'patient'; text: string; sourceLang: string; targetLang: string },
  ) =>
    request<ConsultationTurn>(
      `/api/doctor/consultations/${consultationId}/turn`,
      {
        method: 'POST',
        body: JSON.stringify({
          speaker: turn.speaker,
          text: turn.text,
          source_lang: turn.sourceLang,
          target_lang: turn.targetLang,
        }),
      },
      true,
    ),

  endConsultation: (consultationId: string) =>
    request<LiveConsultation>(
      `/api/doctor/consultations/${consultationId}/end`,
      { method: 'POST' },
      true,
    ),

  /** WebSocket URL for the live queue. Token travels as a query param because
   *  browsers cannot set headers on a WS handshake. */
  doctorSocketUrl(): string | null {
    const token = readToken();
    if (!token) return null;
    return `${API_BASE.replace(/^http/, 'ws')}/api/ws/doctor?token=${encodeURIComponent(token)}`;
  },
};

export const RISK_LABEL: Record<RiskState, string> = {
  LOW_RISK: 'Low risk',
  UNCERTAIN: 'Uncertain',
  URGENT: 'Urgent',
};
