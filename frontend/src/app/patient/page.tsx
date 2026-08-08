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
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import {
  ApiError,
  api,
  type ChatMessage,
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

type Phase = 'language' | 'conversation' | 'waiting' | 'prescription';

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
    <div className="min-h-screen bg-paper">
      <header className="sticky top-0 z-10 border-b border-rule bg-paper/95 backdrop-blur">
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

      <main className="mx-auto max-w-2xl px-5 pb-32 pt-6">
        {phase === 'conversation' && (
          <Conversation
            messages={messages}
            sending={sending}
            error={error}
            lastFailedMessage={lastFailedMessage}
            clinicalState={clinicalState}
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
          />
        )}

        {phase === 'prescription' && prescription && (
          <PrescriptionView
            prescription={prescription}
            languages={languages}
            activeLanguage={prescriptionLang}
            loading={loadingPrescription}
            onLanguageChange={changePrescriptionLanguage}
          />
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
  clinicalState,
  language,
  onRetry,
  transcriptEndRef,
}: {
  messages: ChatMessage[];
  sending: boolean;
  error: string | null;
  lastFailedMessage: string | null;
  clinicalState: TriageCase | null;
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

      {clinicalState && clinicalState.symptoms.length > 0 && (
        <section className="border border-rule bg-surface px-4 py-4">
          <h2 className="label-meta">Recorded so far</h2>
          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 text-[13px]">
            <SummaryPair label="Symptoms" value={clinicalState.symptoms.join(', ')} />
            <SummaryPair label="Duration" value={clinicalState.duration} />
            <SummaryPair label="Severity" value={clinicalState.severity} />
            <SummaryPair
              label="Allergies"
              value={
                clinicalState.allergies.length
                  ? clinicalState.allergies.join(', ')
                  : clinicalState.allergies_confirmed
                    ? 'None reported'
                    : ''
              }
            />
          </dl>
          <p className="mt-4 border-t border-rule pt-3 text-[12px] leading-relaxed text-ink-faint">
            Only what you have told us is recorded. Nothing here is a diagnosis.
          </p>
        </section>
      )}
    </div>
  );
}

function SummaryPair({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="label-meta">{label}</dt>
      <dd className={cx('mt-0.5', value ? 'text-ink' : 'text-ink-faint')}>
        {value || 'Not yet known'}
      </dd>
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

function WaitingRoom({
  urgent,
  urgentGuidance,
  message,
  rejected,
  loading,
}: {
  urgent: boolean;
  urgentGuidance: string | null;
  message: string;
  rejected: boolean;
  loading: boolean;
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
        <p className="mt-5 border-t border-risk-urgent/20 pt-4 text-[13px] leading-relaxed text-ink-muted">
          A doctor has been alerted to your case. No prescription will be issued through
          this service for an emergency presentation — do not wait for one.
        </p>
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

function PrescriptionView({
  prescription,
  languages,
  activeLanguage,
  loading,
  onLanguageChange,
}: {
  prescription: PresentedPrescription;
  languages: Language[];
  activeLanguage: string;
  loading: boolean;
  onLanguageChange: (code: string) => void;
}) {
  const localised = activeLanguage !== 'en';

  return (
    <div className="animate-rise space-y-8">
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
                  {localised && med.frequency_localised && (
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
                    {localised && med.instructions_localised && (
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
          {localised && prescription.instructions_localised && (
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
  );
}
