const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

export interface Medication {
  name: string;
  dosage: string;
  frequency: string;
  duration: string;
  instructions: string;
}

export interface ReferralInfo {
  specialty: string;
  referral_notes: string;
  doctor_name: string;
  created_at: string;
}

export interface Prescription {
  prescription_id: string;
  case_id: string;
  status: 'DRAFT' | 'APPROVED' | 'MODIFIED' | 'REJECTED' | 'NEEDS_REVIEW';
  medications: Medication[];
  instructions: string;
  doctor_id?: string;
  doctor_notes?: string;
  approved_at?: string;
  is_ai_draft: boolean;
  referral?: ReferralInfo;
  language?: string;
}

export interface ChatMessage {
  sender: 'patient' | 'ai';
  text: string;
  timestamp: string;
}

export interface Appointment {
  appointment_id: string;
  case_id: string;
  patient_id: string;
  doctor_id: string;
  doctor_name: string;
  type: 'OPTIONAL_CONSULT' | 'URGENT_EMERGENCY' | 'DOCTOR_SCHEDULED_OFFLINE';
  slot_time: string;
  clinic_location: string;
  status: string;
  notes: string;
  created_at: string;
}

export type SeverityLevel = 'MILD' | 'MODERATE' | 'SEVERE';

export interface TriageCase {
  case_id: string;
  session_id: string;
  patient_id: string;
  symptoms: string[];
  associated_symptoms: string[];
  duration: string;
  severity: string;
  severity_level: SeverityLevel;
  medical_history: string[];
  medications: string[];
  allergies: string[];
  red_flags: string[];
  missing_information: string[];
  triage_status: 'LOW_RISK' | 'UNCERTAIN' | 'URGENT';
  review_status: 'PENDING' | 'APPROVED' | 'MODIFIED' | 'REJECTED' | 'NEEDS_REVIEW' | 'REFERRED' | 'OFFLINE_SCHEDULED';
  summary_en: string;
  transcript: ChatMessage[];
  prescription_draft_id?: string;
  appointment_id?: string;
  referral?: ReferralInfo;
  created_at: string;
  updated_at: string;
}

export interface PatientSession {
  session_id: string;
  patient_id: string;
  preferred_language: string;
  status: string;
  awaiting_final_confirmation?: boolean;
  created_at: string;
}

export interface TriageResponse {
  session_id: string;
  ai_response: string;
  language: string;
  is_complete: boolean;
  triage_status: 'LOW_RISK' | 'UNCERTAIN' | 'URGENT';
  severity_level: SeverityLevel;
  missing_information: string[];
  case_id?: string;
  auto_booked_appointment?: Appointment;
  recommend_appointment: boolean;
}

export const api = {
  // Doctor Auth Login
  async doctorLogin(username: string, password: string) {
    const res = await fetch(`${API_BASE}/api/auth/doctor/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) throw new Error('Invalid doctor username or password');
    const data = await res.json();
    if (typeof window !== 'undefined') {
      localStorage.setItem('doctor_token', data.token);
      localStorage.setItem('doctor_name', data.doctor_name);
    }
    return data;
  },

  isDoctorAuthenticated(): boolean {
    if (typeof window === 'undefined') return false;
    return !!localStorage.getItem('doctor_token');
  },

  doctorLogout() {
    if (typeof window !== 'undefined') {
      localStorage.removeItem('doctor_token');
      localStorage.removeItem('doctor_name');
    }
  },

  // Start Session
  async startSession(language: string = 'en'): Promise<PatientSession> {
    const res = await fetch(`${API_BASE}/api/patient/session`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preferred_language: language }),
    });
    if (!res.ok) throw new Error('Failed to start session');
    return res.json();
  },

  // Send Message
  async sendMessage(sessionId: string, message: string): Promise<TriageResponse> {
    const res = await fetch(`${API_BASE}/api/triage/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message }),
    });
    if (!res.ok) throw new Error('Failed to send message');
    return res.json();
  },

  // Get Doctor Queue Cases
  async getDoctorCases(riskFilter?: string, statusFilter?: string): Promise<TriageCase[]> {
    let url = `${API_BASE}/api/doctor/cases`;
    const params = new URLSearchParams();
    if (riskFilter) params.append('risk_filter', riskFilter);
    if (statusFilter) params.append('status_filter', statusFilter);
    if (params.toString()) url += `?${params.toString()}`;

    const res = await fetch(url);
    if (!res.ok) throw new Error('Failed to fetch doctor cases');
    return res.json();
  },

  // Get Case Detail
  async getCaseDetail(caseId: string): Promise<{ case: TriageCase; prescription_draft: Prescription; appointment?: Appointment; referral?: ReferralInfo }> {
    const res = await fetch(`${API_BASE}/api/doctor/cases/${caseId}`);
    if (!res.ok) throw new Error('Failed to fetch case detail');
    return res.json();
  },

  // Submit Doctor Decision (Approve / Modify / Reject / Refer / Offline Appointment)
  async submitDoctorDecision(
    caseId: string,
    decision: 'APPROVE' | 'MODIFY' | 'REJECT' | 'NEEDS_REVIEW' | 'REFERRAL' | 'OFFLINE_APPOINTMENT',
    notes?: string,
    modifiedMedications?: Medication[],
    modifiedInstructions?: string,
    referralSpecialty?: string,
    referralNotes?: string,
    offlineAppointmentTime?: string,
    offlineClinicLocation?: string
  ) {
    const res = await fetch(`${API_BASE}/api/doctor/cases/${caseId}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        decision,
        doctor_id: 'DR-101',
        doctor_name: localStorage.getItem('doctor_name') || 'Dr. Sharma, MD',
        notes,
        modified_medications: modifiedMedications,
        modified_instructions: modifiedInstructions,
        referral_specialty: referralSpecialty,
        referral_notes: referralNotes,
        offline_appointment_time: offlineAppointmentTime,
        offline_clinic_location: offlineClinicLocation
      }),
    });
    if (!res.ok) throw new Error('Failed to submit decision');
    return res.json();
  },

  // Get Prescription
  async getPrescription(prescriptionId: string, lang: string = 'en'): Promise<Prescription> {
    const res = await fetch(`${API_BASE}/api/prescriptions/${prescriptionId}?lang=${lang}`);
    if (!res.ok) throw new Error('Failed to fetch prescription');
    return res.json();
  },

  // Book Appointment
  async bookAppointment(caseId: string, slotTime?: string, clinicLocation?: string): Promise<Appointment> {
    const res = await fetch(`${API_BASE}/api/appointments/book`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ case_id: caseId, slot_time: slotTime, clinic_location: clinicLocation }),
    });
    if (!res.ok) throw new Error('Failed to book appointment');
    return res.json();
  }
};
