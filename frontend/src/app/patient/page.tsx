'use client';

/**
 * Patient interface. Mobile-first, four phases:
 *
 *   language -> conversation -> waiting -> prescription
 *
 * Two things this screen must never do: imply the AI has prescribed anything,
 * or show medication before a doctor has finalised it. The waiting phase is
 * therefore explicit that a human is reviewing, and prescription content is
 * only ever fetched from the guarded endpoint after `prescription_available`.
 *
 * A third thing it must never do: book an appointment on the patient's
 * behalf. recommend_appointment (URGENT or UNCERTAIN) only ever offers a
 * button — booking happens through an explicit patient confirmation via
 * api.bookAppointment, never automatically.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import {
  ApiError,
  api,
  type Appointment,
  type ChatMessage,
  type ClinicInfo,
  type Language,
  type PatientStatus,
  type PresentedPrescription,
  type RiskState,
  type TriageCase,
} from '@/lib/api';
import {
  SpeechInput,
  isRecognitionSupported,
  isSynthesisSupported,
  speak,
  stopSpeaking,
} from '@/lib/speech';
import {
  ErrorNotice,
  RiskBadge,
  SectionTitle,
  Spinner,
  StatusRail,
  cx,
} from '@/components/ui/clinical';

type Phase = 'language' | 'conversation' | 'waiting' | 'prescription' | 'visit-report';

const POLL_INTERVAL_MS = 4000;

export default function PatientPage() {
  const [phase, setPhase] = useState<Phase>('language');
  const [languages, setLanguages] = useState<Language[]>([]);
  const [language, setLanguage] = useState<Language | null>(null);

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [clinicalState, setClinicalState] = useState<TriageCase | null>(null);
  const [patientStatus, setPatientStatus] = useState<PatientStatus>('COLLECTING_INFORMATION');
  const [risk, setRisk] = useState<RiskState>('UNCERTAIN');
  const [urgentGuidance, setUrgentGuidance] = useState<string | null>(null);

  const [draft, setDraft] = useState('');
  const [interim, setInterim] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastFailedMessage, setLastFailedMessage] = useState<string | null>(null);

  const [listening, setListening] = useState(false);
  const [voiceReplies, setVoiceReplies] = useState(false);

  const [statusMessage, setStatusMessage] = useState('');
  const [prescription, setPrescription] = useState<PresentedPrescription | null>(null);
  const [prescriptionLang, setPrescriptionLang] = useState<string>('en');
  const [loadingPrescription, setLoadingPrescription] = useState(false);
  const [reviewRejected, setReviewRejected] = useState(false);

  const [recommendAppointment, setRecommendAppointment] = useState(false);
  const [recommendedSpecialty, setRecommendedSpecialty] = useState<string | null>(null);
  const [appointment, setAppointment] = useState<Appointment | null>(null);
  const [bookingAppointment, setBookingAppointment] = useState(false);

  const [visitReport, setVisitReport] = useState<string | null>(null);
  const [clinicInfo, setClinicInfo] = useState<ClinicInfo | null>(null);

  const speechRef = useRef<SpeechInput | null>(null);
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);

  const speechSupported = useMemo(() => isRecognitionSupported(), []);
  const synthesisSupported = useMemo(() => isSynthesisSupported(), []);

  /* ------------------------------------------------------------- bootstrap */

  useEffect(() => {
    api
      .listLanguages()
      .then(setLanguages)
      .catch(() => setError('Could not load languages. Is the clinical service running?'));
    // Best-effort: only needed for the printable prescription's letterhead,
    // not for anything else on this page — a failure here shouldn't block
    // the actual clinical flow.
    api.getClinicInfo().then(setClinicInfo).catch(() => {});
    speechRef.current = new SpeechInput();
    return () => {
      speechRef.current?.stop();
      stopSpeaking();
    };
  }, []);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, sending]);

  /* --------------------------------------------------------- waiting poll */

  useEffect(() => {
    if (phase !== 'waiting' || !sessionId) return;

    let cancelled = false;

    const poll = async () => {
      try {
        const status = await api.patientStatus(sessionId);
        if (cancelled) return;

        setPatientStatus(status.patient_status);
        setStatusMessage(status.message);
        setRisk(status.triage_status);
        setRecommendAppointment(status.recommend_appointment);
        setRecommendedSpecialty(status.recommended_specialty);
        if (status.appointment) setAppointment(status.appointment);

        // A completed in-person consultation is the most recent, most
        // complete outcome available — it takes priority over the async
        // prescription track, which a case may or may not also have.
        if (status.visit_report_available && status.visit_report) {
          setVisitReport(status.visit_report);
          setPhase('visit-report');
          return;
        }

        if (status.rejected) {
          setReviewRejected(true);
          return;
        }

        if (status.prescription_available && status.prescription_id) {
          setLoadingPrescription(true);
          const initial = language?.code ?? 'en';
          const presented = await api.getPrescription(status.prescription_id, initial);
          if (cancelled) return;
          setPrescription(presented);
          setPrescriptionLang(initial);
          setLoadingPrescription(false);
          setPhase('prescription');
        }
      } catch {
        // Transient failures are expected while polling; the next tick retries.
      }
    };

    poll();
    const timer = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [phase, sessionId, language]);

  /* ----------------------------------------------------------- actions */

  const beginSession = useCallback(async (selected: Language) => {
    setError(null);
    try {
      const session = await api.startSession(selected.code);
      setLanguage(selected);
      setSessionId(session.session_id);
      setPatientStatus(session.status);
      setPrescriptionLang(selected.code);
      setPhase('conversation');

      // The backend seeds the opening greeting into the transcript when the
      // session is created; read it rather than sending a dummy message.
      const state = await api.getTriageState(session.session_id).catch(() => null);
      if (state?.case) {
        setMessages(state.case.transcript);
        setClinicalState(state.case);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the session.');
    }
  }, []);

  const submitMessage = useCallback(
    async (text: string) => {
      if (!sessionId || !text.trim() || sending) return;

      const value = text.trim();
      setDraft('');
      setInterim('');
      setError(null);
      setLastFailedMessage(null);
      setSending(true);

      // Optimistic echo so the patient sees their own words immediately.
      setMessages((prev) => [
        ...prev,
        { sender: 'patient', text: value, timestamp: new Date().toISOString() },
      ]);

      try {
        const response = await api.sendMessage(sessionId, value);

        setMessages(response.clinical_state?.transcript ?? []);
        setClinicalState(response.clinical_state);
        setPatientStatus(response.patient_status);
        setRisk(response.triage_status);
        setRecommendAppointment(response.recommend_appointment);
        setRecommendedSpecialty(response.recommended_specialty);

        if (response.urgent_guidance) {
          setUrgentGuidance(response.urgent_guidance);
        }

        if (voiceReplies && language) {
          speak(response.ai_response, language.speech_tag);
        }

        if (response.is_complete) {
          if (response.triage_status === 'URGENT') {
            // Escalated cases are already handed off server-side.
            setPhase('waiting');
          } else {
            await api.assess(sessionId);
            await api.handOff(sessionId);
            setPhase('waiting');
          }
        }
      } catch (err) {
        // Session state is preserved server-side, so retry is safe.
        setLastFailedMessage(value);
        setMessages((prev) => prev.slice(0, -1));
        setError(
          err instanceof ApiError
            ? err.message
            : 'Something went wrong. Your information is saved — please try again.',
        );
      } finally {
        setSending(false);
      }
    },
    [sessionId, sending, voiceReplies, language],
  );

  const confirmAppointment = useCallback(async () => {
    if (!clinicalState || bookingAppointment) return;
    setBookingAppointment(true);
    setError(null);
    try {
      const booked = await api.bookAppointment(
        clinicalState.case_id,
        undefined,
        undefined,
        recommendedSpecialty ?? undefined,
      );
      setAppointment(booked);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not book the appointment.');
    } finally {
      setBookingAppointment(false);
    }
  }, [clinicalState, recommendedSpecialty, bookingAppointment]);

  const toggleListening = useCallback(() => {
    if (!language || !speechRef.current) return;

    if (listening) {
      speechRef.current.stop();
      setListening(false);
      return;
    }

    stopSpeaking();
    const started = speechRef.current.start(language.speech_tag, {
      onResult: (transcript, isFinal) => {
        if (isFinal) {
          setDraft((prev) => (prev ? `${prev} ${transcript}` : transcript));
          setInterim('');
        } else {
          setInterim(transcript);
        }
      },
      onError: (message) => {
        setError(message);
        setListening(false);
        setInterim('');
      },
      onEnd: () => {
        setListening(false);
        setInterim('');
      },
    });
    if (started) setListening(true);
  }, [language, listening]);

  const changePrescriptionLanguage = useCallback(
    async (code: string) => {
      if (!prescription) return;
      setLoadingPrescription(true);
      try {
        // Re-fetches a presentation of the SAME canonical record. The backend
        // never regenerates clinical content on a language change.
        const next = await api.getPrescription(prescription.prescription_id, code);
        setPrescription(next);
        setPrescriptionLang(code);
      } catch {
        setError('Could not load that language. Showing the previous version.');
      } finally {
        setLoadingPrescription(false);
      }
    },
    [prescription],
  );

  /* -------------------------------------------------------------- render */

  if (phase === 'language') {
    return (
      <LanguageChooser
        languages={languages}
        error={error}
        onSelect={beginSession}
      />
    );
  }

  return (
    <div className="min-h-screen bg-paper print:min-h-0 print:bg-white">
      <header className="sticky top-0 z-10 border-b border-rule bg-paper/95 backdrop-blur print:hidden">
        <div className="mx-auto flex max-w-2xl items-center justify-between gap-4 px-5 py-3">
          <Link href="/" className="text-[13px] font-semibold tracking-tight text-ink">
            Clinical Assistant
          </Link>
          <div className="flex items-center gap-3">
            {risk !== 'UNCERTAIN' && <RiskBadge risk={risk} size="sm" />}
            <span className="label-meta">{language?.native_name}</span>
          </div>
        </div>
        <div className="mx-auto max-w-2xl px-5 pb-3">
          <StatusRail current={patientStatus} />
        </div>
      </header>

      <main className="mx-auto max-w-2xl px-5 pb-32 pt-6 print:max-w-none print:p-0">
        {phase === 'conversation' && (
          <Conversation
            messages={messages}
            sending={sending}
            error={error}
            lastFailedMessage={lastFailedMessage}
            language={language}
            onRetry={() => lastFailedMessage && submitMessage(lastFailedMessage)}
            transcriptEndRef={transcriptEndRef}
          />
        )}

        {phase === 'waiting' && (
          <WaitingRoom
            urgent={risk === 'URGENT'}
            urgentGuidance={urgentGuidance}
            message={statusMessage}
            rejected={reviewRejected}
            loading={loadingPrescription}
            recommendAppointment={recommendAppointment}
            recommendedSpecialty={recommendedSpecialty}
            appointment={appointment}
            bookingAppointment={bookingAppointment}
            onBookAppointment={confirmAppointment}
          />
        )}

        {phase === 'prescription' && prescription && (
          <PrescriptionView
            prescription={prescription}
            languages={languages}
            activeLanguage={prescriptionLang}
            loading={loadingPrescription}
            onLanguageChange={changePrescriptionLanguage}
            clinicInfo={clinicInfo}
            patientId={clinicalState?.patient_id ?? null}
          />
        )}

        {phase === 'visit-report' && visitReport && (
          <VisitReportView report={visitReport} language={language} />
        )}
      </main>

      {phase === 'conversation' && (
        <Composer
          draft={draft}
          interim={interim}
          sending={sending}
          listening={listening}
          speechSupported={speechSupported}
          synthesisSupported={synthesisSupported}
          voiceReplies={voiceReplies}
          onDraftChange={setDraft}
          onToggleVoiceReplies={() => {
            stopSpeaking();
            setVoiceReplies((v) => !v);
          }}
          onToggleListening={toggleListening}
          onSubmit={() => submitMessage(draft)}
        />
      )}
    </div>
  );
}

/* ====================================================================== */

function LanguageChooser({
  languages,
  error,
  onSelect,
}: {
  languages: Language[];
  error: string | null;
  onSelect: (language: Language) => void;
}) {
  return (
    <div className="flex min-h-screen flex-col bg-paper">
      <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center px-5 py-16">
        <p className="label-meta">Multilingual clinical intake</p>
        <h1 className="mt-4 text-display font-semibold text-ink">
          Tell us how you
          <br />
          are feeling.
        </h1>
        <p className="mt-5 max-w-reading text-body text-ink-muted">
          Speak or type in your own language. We will collect your symptoms and pass them
          to a doctor. A doctor reviews every case and decides on any prescription.
        </p>

        <div className="mt-12">
          <h2 className="label-meta">Choose your language</h2>
          <div className="mt-4 grid grid-cols-2 gap-px border border-rule bg-rule sm:grid-cols-3">
            {languages.map((lang) => (
              <button
                key={lang.code}
                onClick={() => onSelect(lang)}
                className="group flex flex-col items-start gap-1 bg-surface px-4 py-5 text-left transition-colors hover:bg-accent-soft"
              >
                <span className="text-[17px] font-medium text-ink">{lang.native_name}</span>
                <span className="text-[12px] text-ink-faint">{lang.english_name}</span>
              </button>
            ))}
            {languages.length === 0 && (
              <div className="col-span-full bg-surface px-4 py-8">
                <Spinner label="Loading languages" />
              </div>
            )}
          </div>
        </div>

        {error && (
          <div className="mt-6">
            <ErrorNotice message={error} />
          </div>
        )}

        <p className="mt-12 max-w-reading text-[13px] leading-relaxed text-ink-faint">
          This service does not diagnose and does not replace a doctor. If you are having a
          medical emergency, contact emergency services immediately.
        </p>
      </div>

      <footer className="border-t border-rule">
        <div className="mx-auto flex max-w-2xl items-center justify-between px-5 py-4">
          <span className="label-meta">Patient</span>
          <Link href="/doctor" className="text-[13px] text-ink-muted underline underline-offset-2">
            Doctor sign in
          </Link>
        </div>
      </footer>
    </div>
  );
}

/* ====================================================================== */

function Conversation({
  messages,
  sending,
  error,
  lastFailedMessage,
  language,
  onRetry,
  transcriptEndRef,
}: {
  messages: ChatMessage[];
  sending: boolean;
  error: string | null;
  lastFailedMessage: string | null;
  language: Language | null;
  onRetry: () => void;
  transcriptEndRef: React.RefObject<HTMLDivElement>;
}) {
  const visible = messages.filter((m) => m.text.trim() && m.text !== '​');

  return (
    <div className="space-y-6">
      <div className="space-y-5">
        {visible.map((message, index) => (
          <div
            key={`${message.timestamp}-${index}`}
            className={cx(
              'animate-rise',
              message.sender === 'patient' ? 'flex justify-end' : 'flex justify-start',
            )}
          >
            <div className={cx('max-w-[85%]', message.sender === 'patient' && 'text-right')}>
              <span className="label-meta">
                {message.sender === 'patient' ? 'You' : 'Assistant'}
              </span>
              <p
                lang={message.sender === 'ai' ? language?.code : undefined}
                className={cx(
                  'mt-1.5 whitespace-pre-wrap px-4 py-3 text-[16px] leading-relaxed',
                  message.sender === 'patient'
                    ? 'border border-rule bg-surface-sunken text-ink'
                    : 'border-l-2 border-accent bg-surface text-ink',
                )}
              >
                {message.text}
              </p>
            </div>
          </div>
        ))}

        {sending && (
          <div className="flex justify-start animate-rise">
            <div className="border-l-2 border-rule px-4 py-3">
              <Spinner label="Thinking" />
            </div>
          </div>
        )}
        <div ref={transcriptEndRef} />
      </div>

      {error && (
        <ErrorNotice message={error} onRetry={lastFailedMessage ? onRetry : undefined} />
      )}
    </div>
  );
}

/* ====================================================================== */

function Composer({
  draft,
  interim,
  sending,
  listening,
  speechSupported,
  synthesisSupported,
  voiceReplies,
  onDraftChange,
  onToggleVoiceReplies,
  onToggleListening,
  onSubmit,
}: {
  draft: string;
  interim: string;
  sending: boolean;
  listening: boolean;
  speechSupported: boolean;
  synthesisSupported: boolean;
  voiceReplies: boolean;
  onDraftChange: (value: string) => void;
  onToggleVoiceReplies: () => void;
  onToggleListening: () => void;
  onSubmit: () => void;
}) {
  return (
    <div className="fixed inset-x-0 bottom-0 border-t border-rule bg-paper/95 backdrop-blur">
      <div className="mx-auto max-w-2xl px-5 py-4">
        {listening && (
          <p className="mb-2 flex items-center gap-2 text-[12px] text-accent">
            <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse-dot" aria-hidden />
            Listening{interim && <span className="text-ink-faint">— {interim}</span>}
          </p>
        )}

        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(e) => onDraftChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                onSubmit();
              }
            }}
            rows={1}
            placeholder="Describe how you feel…"
            disabled={sending}
            className="field max-h-32 min-h-[46px] flex-1 resize-none"
          />

          {speechSupported && (
            <button
              type="button"
              onClick={onToggleListening}
              aria-pressed={listening}
              aria-label={listening ? 'Stop voice input' : 'Start voice input'}
              className={cx(
                'btn h-[46px] w-[46px] shrink-0 p-0',
                listening
                  ? 'border-accent bg-accent text-white'
                  : 'border-rule-strong bg-surface text-ink-muted hover:bg-surface-sunken',
              )}
            >
              <MicIcon />
            </button>
          )}

          <button
            type="button"
            onClick={onSubmit}
            disabled={sending || !draft.trim()}
            className="btn-primary h-[46px] shrink-0"
          >
            Send
          </button>
        </div>

        {synthesisSupported && (
          <button
            type="button"
            onClick={onToggleVoiceReplies}
            className="mt-2 text-[12px] text-ink-faint underline underline-offset-2"
          >
            {voiceReplies ? 'Turn off spoken replies' : 'Read replies aloud'}
          </button>
        )}
      </div>
    </div>
  );
}

function MicIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" strokeLinecap="round" />
    </svg>
  );
}

/* ====================================================================== */

function AppointmentAction({
  tone,
  appointment,
  recommendedSpecialty,
  bookingAppointment,
  onBookAppointment,
  bookLabel,
  bookedLabel,
}: {
  tone: 'urgent' | 'uncertain';
  appointment: Appointment | null;
  recommendedSpecialty: string | null;
  bookingAppointment: boolean;
  onBookAppointment: () => void;
  bookLabel: string;
  bookedLabel: string;
}) {
  const border = tone === 'urgent' ? 'border-risk-urgent/30' : 'border-risk-uncertain/30';
  const label = tone === 'urgent' ? 'text-risk-urgent' : 'text-risk-uncertain';

  if (appointment) {
    return (
      <div className={cx('mt-5 border bg-surface px-4 py-3', border)}>
        <p className={cx('label-meta', label)}>{bookedLabel}</p>
        <p className="mt-1 text-[14px] text-ink">
          {appointment.slot_time} · {appointment.clinic_location}
        </p>
        {appointment.specialty && (
          <p className="mt-1 text-[13px] text-ink-muted">Specialist: {appointment.specialty}</p>
        )}
      </div>
    );
  }

  return (
    <div className="mt-5">
      {recommendedSpecialty && (
        <p className="mb-2 text-[13px] text-ink-muted">
          Recommended specialist: {recommendedSpecialty}
        </p>
      )}
      <button type="button" onClick={onBookAppointment} disabled={bookingAppointment} className="btn-primary">
        {bookingAppointment ? 'Booking…' : bookLabel}
      </button>
    </div>
  );
}

function WaitingRoom({
  urgent,
  urgentGuidance,
  message,
  rejected,
  loading,
  recommendAppointment,
  recommendedSpecialty,
  appointment,
  bookingAppointment,
  onBookAppointment,
}: {
  urgent: boolean;
  urgentGuidance: string | null;
  message: string;
  rejected: boolean;
  loading: boolean;
  recommendAppointment: boolean;
  recommendedSpecialty: string | null;
  appointment: Appointment | null;
  bookingAppointment: boolean;
  onBookAppointment: () => void;
}) {
  if (urgent) {
    return (
      <div className="animate-rise border-l-2 border-risk-urgent bg-risk-urgent-soft px-5 py-6">
        <p className="label-meta text-risk-urgent">Urgent — seek care now</p>
        <h1 className="mt-3 text-title font-semibold text-ink">
          Please get emergency medical help immediately.
        </h1>
        <p className="mt-4 text-body leading-relaxed text-ink">
          {urgentGuidance ||
            'The symptoms you described need emergency medical care. Contact emergency services or go to your nearest emergency department now.'}
        </p>

        <AppointmentAction
          tone="urgent"
          appointment={appointment}
          recommendedSpecialty={recommendedSpecialty}
          bookingAppointment={bookingAppointment}
          onBookAppointment={onBookAppointment}
          bookLabel="Confirm & book emergency appointment now"
          bookedLabel="Emergency appointment confirmed"
        />

        <p className="mt-5 border-t border-risk-urgent/20 pt-4 text-[13px] leading-relaxed text-ink-muted">
          A doctor has been alerted to your case. Booking above does not replace calling
          emergency services — do not wait for a prescription through this service.
        </p>
      </div>
    );
  }

  if (recommendAppointment) {
    return (
      <div className="animate-rise border-l-2 border-risk-uncertain bg-risk-uncertain-soft px-5 py-6">
        <p className="label-meta text-risk-uncertain">In-person visit recommended</p>
        <h1 className="mt-3 text-title font-semibold text-ink">
          Your symptoms need an in-person evaluation.
        </h1>
        <p className="mt-4 text-body leading-relaxed text-ink">
          We are not able to draft a home prescription from the information gathered so far.
          Please book an appointment so a doctor can examine you directly.
        </p>

        <AppointmentAction
          tone="uncertain"
          appointment={appointment}
          recommendedSpecialty={recommendedSpecialty}
          bookingAppointment={bookingAppointment}
          onBookAppointment={onBookAppointment}
          bookLabel="Book in-person appointment"
          bookedLabel="Appointment booked"
        />
      </div>
    );
  }

  if (rejected) {
    return (
      <div className="animate-rise">
        <p className="label-meta">Review complete</p>
        <h1 className="mt-3 text-title font-semibold text-ink">
          Your doctor has reviewed your case.
        </h1>
        <p className="mt-4 max-w-reading text-body leading-relaxed text-ink-muted">
          {message ||
            'Your doctor did not issue a prescription for this case. Please follow up with your doctor or clinic for next steps.'}
        </p>
      </div>
    );
  }

  return (
    <div className="animate-rise">
      <p className="label-meta">Waiting for doctor</p>
      <h1 className="mt-3 text-title font-semibold text-ink">
        Your information has been sent for doctor review.
      </h1>
      <p className="mt-4 max-w-reading text-body leading-relaxed text-ink-muted">
        {message ||
          'You will receive your prescription here after the doctor has reviewed your case. You can keep this page open.'}
      </p>

      <div className="mt-8 flex items-center gap-3 border-t border-rule pt-6">
        <Spinner />
        <span className="text-[13px] text-ink-muted">
          {loading ? 'Loading your prescription' : 'Checking for an update'}
        </span>
      </div>

      <p className="mt-10 max-w-reading text-[13px] leading-relaxed text-ink-faint">
        Nothing has been prescribed yet. Only a doctor can issue your prescription.
      </p>
    </div>
  );
}

/* ====================================================================== */

function VisitReportView({ report, language }: { report: string; language: Language | null }) {
  return (
    <div className="animate-rise space-y-6">
      <div>
        <p className="label-meta text-risk-low">From your in-person visit</p>
        <h1 className="mt-3 text-title font-semibold text-ink">Visit report</h1>
      </div>
      <p
        className="max-w-reading whitespace-pre-wrap text-body leading-relaxed text-ink"
        lang={language?.code}
      >
        {report}
      </p>
      <p className="border-t border-rule pt-6 text-[13px] leading-relaxed text-ink-faint">
        This is a record of your consultation, not a prescription. If your doctor prescribed
        anything during the visit, they will have given it to you directly.
      </p>
    </div>
  );
}

/* ====================================================================== */

/**
 * Standard prescription-pad layout for printing/PDF export. `hidden
 * print:block` — invisible on screen, only enters the DOM's rendered layout
 * when printing (see globals.css for the accompanying @page rule). Always
 * English/canonical, regardless of the on-screen display language: this is
 * the document a pharmacist reads, not a patient-facing translation.
 *
 * Deliberately plain black-on-white with rules instead of the app's colour
 * system — background colours are not reliable in print output unless the
 * user has "background graphics" enabled, so nothing here depends on one.
 */
function PrintablePrescription({
  prescription,
  clinicInfo,
  patientId,
}: {
  prescription: PresentedPrescription;
  clinicInfo: ClinicInfo | null;
  patientId: string | null;
}) {
  const approvedAt = prescription.approved_at ? new Date(prescription.approved_at) : null;

  return (
    <div className="hidden print:block print:text-black">
      <header className="border-b-2 border-black pb-3">
        <h1 className="text-xl font-bold">
          {clinicInfo?.hospital_name ?? 'Clinical Assistant General Hospital'}
        </h1>
        <p className="mt-1 text-[12px]">{clinicInfo?.hospital_address}</p>
        <p className="text-[12px]">
          {clinicInfo?.hospital_phone}
          {clinicInfo?.hospital_registration_no &&
            ` · Reg. No: ${clinicInfo.hospital_registration_no}`}
        </p>
      </header>

      <div className="mt-4 flex justify-between text-[13px]">
        <div>
          <p>
            <span className="font-semibold">Patient ID:</span> {patientId ?? '—'}
          </p>
          <p>
            <span className="font-semibold">Prescription ID:</span>{' '}
            {prescription.prescription_id}
          </p>
        </div>
        <div className="text-right">
          <p>
            <span className="font-semibold">Date:</span>{' '}
            {(approvedAt ?? new Date()).toLocaleDateString()}
          </p>
          <p>
            <span className="font-semibold">Status:</span>{' '}
            {prescription.status === 'MODIFIED' ? 'Approved (modified by doctor)' : 'Approved'}
          </p>
        </div>
      </div>

      <div className="mt-6">
        <p className="text-2xl font-serif">℞</p>
        <table className="mt-2 w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-black text-left">
              <th className="py-1 pr-2">Medicine</th>
              <th className="py-1 pr-2">Dosage</th>
              <th className="py-1 pr-2">Frequency</th>
              <th className="py-1 pr-2">Duration</th>
              <th className="py-1">Instructions</th>
            </tr>
          </thead>
          <tbody>
            {prescription.medications.map((med, index) => (
              <tr key={`${med.name}-${index}`} className="border-b border-rule">
                <td className="py-2 pr-2 font-semibold">{med.name}</td>
                <td className="py-2 pr-2 font-mono">{med.dosage}</td>
                <td className="py-2 pr-2">{med.frequency}</td>
                <td className="py-2 pr-2">{med.duration}</td>
                <td className="py-2">{med.instructions}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {prescription.instructions && (
        <div className="mt-4 text-[13px]">
          <p className="font-semibold">General advice</p>
          <p className="mt-1">{prescription.instructions}</p>
        </div>
      )}

      {prescription.doctor_notes && (
        <div className="mt-4 text-[13px]">
          <p className="font-semibold">Note from your doctor</p>
          <p className="mt-1">{prescription.doctor_notes}</p>
        </div>
      )}

      <div className="mt-16 flex justify-end">
        <div className="w-64 text-right text-[13px]">
          <p
            className="text-2xl italic"
            style={{ fontFamily: "'Brush Script MT', 'Segoe Script', cursive" }}
          >
            {prescription.doctor_name ?? clinicInfo?.doctor_name ?? 'Attending Physician'}
          </p>
          <div className="mt-1 border-t border-black pt-1">
            <p className="font-semibold">
              {prescription.doctor_name ?? clinicInfo?.doctor_name}
            </p>
            {clinicInfo?.doctor_qualification && <p>{clinicInfo.doctor_qualification}</p>}
            {clinicInfo?.doctor_registration_no && (
              <p>Reg. No: {clinicInfo.doctor_registration_no}</p>
            )}
            {approvedAt && (
              <p className="mt-1 text-[11px]">
                Digitally authenticated · {approvedAt.toLocaleString()}
              </p>
            )}
          </div>
        </div>
      </div>

      <p className="mt-10 border-t border-black pt-2 text-[10px] leading-relaxed">
        This is a digitally generated prescription, reviewed and approved by the physician named
        above. Medicine names, dosages and durations are reproduced exactly as approved. Prepared
        via a multilingual clinical intake assistant; the assistant does not diagnose or
        prescribe — every clinical decision on this document was made by the physician.
      </p>
    </div>
  );
}

function PrescriptionView({
  prescription,
  languages,
  activeLanguage,
  loading,
  onLanguageChange,
  clinicInfo,
  patientId,
}: {
  prescription: PresentedPrescription;
  languages: Language[];
  activeLanguage: string;
  loading: boolean;
  onLanguageChange: (code: string) => void;
  clinicInfo: ClinicInfo | null;
  patientId: string | null;
}) {
  const localised = activeLanguage !== 'en';

  return (
    <>
      {/* Only present in the DOM for print (see globals.css's print rules) —
          a `print:hidden` ancestor would hide this too if it were nested
          inside the screen-only block below, so it's a sibling instead.
          Always English/canonical: a printed prescription handed to a
          pharmacist should read exactly as the doctor approved it, not in
          whatever display language happened to be selected on screen. */}
      <PrintablePrescription
        prescription={prescription}
        clinicInfo={clinicInfo}
        patientId={patientId}
      />

      <div className="animate-rise space-y-8 print:hidden">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="label-meta text-risk-low">Approved by your doctor</p>
            <h1 className="mt-3 text-title font-semibold text-ink">Your prescription</h1>
            {prescription.doctor_name && (
              <p className="mt-2 text-[13px] text-ink-muted">
                Reviewed by {prescription.doctor_name}
                {prescription.approved_at &&
                  ` · ${new Date(prescription.approved_at).toLocaleString()}`}
              </p>
            )}
          </div>
          <button onClick={() => window.print()} className="btn-secondary shrink-0">
            Print / Download PDF
          </button>
        </div>

        <div>
          <h2 className="label-meta">Show in</h2>
        <div className="mt-2 flex flex-wrap gap-px border border-rule bg-rule">
          {languages.map((lang) => (
            <button
              key={lang.code}
              onClick={() => onLanguageChange(lang.code)}
              disabled={loading}
              className={cx(
                'bg-surface px-3 py-2 text-[13px] transition-colors disabled:opacity-50',
                lang.code === activeLanguage
                  ? 'bg-ink text-white'
                  : 'text-ink-muted hover:bg-surface-sunken',
              )}
            >
              {lang.native_name}
            </button>
          ))}
        </div>
        <p className="mt-2 text-[12px] leading-relaxed text-ink-faint">
          Changing the language only changes how this is written. Your medicines, doses and
          durations stay exactly as your doctor approved them.
        </p>
      </div>

      {prescription.translation_notice && (
        <div className="border border-risk-uncertain/30 bg-risk-uncertain-soft px-4 py-3">
          <p className="text-[13px] leading-relaxed text-risk-uncertain">
            {prescription.translation_notice}
          </p>
        </div>
      )}

      <section>
        <SectionTitle note={`${prescription.medications.length} item(s)`}>
          Medicines
        </SectionTitle>
        <ul className="mt-4 space-y-px bg-rule">
          {prescription.medications.map((med, index) => (
            <li key={`${med.name}-${index}`} className="bg-surface px-4 py-5">
              {/* Name and dosage are never translated — they are copied
                  verbatim from the record the doctor approved. */}
              <p className="text-heading font-semibold text-ink">{med.name}</p>
              <p className="mt-1 font-mono text-[15px] text-ink">{med.dosage}</p>

              <dl className="mt-4 space-y-3 border-t border-rule pt-3 text-[14px]">
                <div>
                  <dt className="label-meta">How often</dt>
                  <dd className="mt-0.5 text-ink" lang={localised ? activeLanguage : undefined}>
                    {localised && med.frequency_localised ? med.frequency_localised : med.frequency}
                  </dd>
                  {/* Only show the English original as a second line when the
                      "translation" actually differs from it — without an
                      LLM, translation can silently fall back to the
                      untranslated source, and showing identical text twice
                      reads as a rendering bug, not a feature. */}
                  {localised && med.frequency_localised && med.frequency_localised !== med.frequency && (
                    <dd className="mt-0.5 text-[12px] text-ink-faint">{med.frequency}</dd>
                  )}
                </div>
                <div>
                  <dt className="label-meta">For how long</dt>
                  <dd className="mt-0.5 font-mono text-ink">{med.duration}</dd>
                </div>
                {med.instructions && (
                  <div>
                    <dt className="label-meta">How to take it</dt>
                    <dd className="mt-0.5 leading-relaxed text-ink" lang={localised ? activeLanguage : undefined}>
                      {localised && med.instructions_localised
                        ? med.instructions_localised
                        : med.instructions}
                    </dd>
                    {localised && med.instructions_localised && med.instructions_localised !== med.instructions && (
                      <dd className="mt-1 text-[12px] leading-relaxed text-ink-faint">
                        {med.instructions}
                      </dd>
                    )}
                  </div>
                )}
              </dl>
            </li>
          ))}
        </ul>
      </section>

      {prescription.instructions && (
        <section>
          <SectionTitle>General advice</SectionTitle>
          <p
            className="mt-3 max-w-reading text-body leading-relaxed text-ink"
            lang={localised ? activeLanguage : undefined}
          >
            {localised && prescription.instructions_localised
              ? prescription.instructions_localised
              : prescription.instructions}
          </p>
          {localised &&
            prescription.instructions_localised &&
            prescription.instructions_localised !== prescription.instructions && (
              <p className="mt-2 max-w-reading text-[12px] leading-relaxed text-ink-faint">
                {prescription.instructions}
              </p>
            )}
        </section>
      )}

      {prescription.doctor_notes && (
        <section>
          <SectionTitle>Note from your doctor</SectionTitle>
          <p className="mt-3 max-w-reading text-body leading-relaxed text-ink">
            {prescription.doctor_notes}
          </p>
        </section>
      )}

        <p className="border-t border-rule pt-6 text-[13px] leading-relaxed text-ink-faint">
          Take this exactly as written. If your symptoms get worse or you feel unwell in a new
          way, contact your doctor or seek urgent care.
        </p>
      </div>
    </>
  );
}
