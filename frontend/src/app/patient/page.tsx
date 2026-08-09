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
  type PatientProfile,
  type PatientHistoryItem,
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

type Phase = 'auth' | 'dashboard' | 'language' | 'conversation' | 'waiting' | 'prescription';

const POLL_INTERVAL_MS = 4000;

export default function PatientPage() {
  const [phase, setPhase] = useState<Phase>('auth');
  const [patientProfile, setPatientProfile] = useState<PatientProfile | null>(null);
  const [history, setHistory] = useState<PatientHistoryItem[]>([]);
  const [authName, setAuthName] = useState('');
  const [authAge, setAuthAge] = useState('');
  const [authError, setAuthError] = useState('');
  const [loadingAuth, setLoadingAuth] = useState(false);
  const [languages, setLanguages] = useState<Language[]>([]);
  const [language, setLanguage] = useState<Language | null>(null);

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [clinicalState, setClinicalState] = useState<TriageCase | null>(null);
  const [patientStatus, setPatientStatus] = useState<PatientStatus>('COLLECTING_INFORMATION');
  const [risk, setRisk] = useState<RiskState>('UNCERTAIN');
  const [urgentGuidance, setUrgentGuidance] = useState<string | null>(null);

  const [draft, setDraft] = useState('');
  const draftRef = useRef('');
  const setDraftWithRef = useCallback((val: string | ((prev: string) => string)) => {
    setDraft((prev) => {
      const next = typeof val === 'function' ? val(prev) : val;
      draftRef.current = next;
      return next;
    });
  }, []);
  const submitMessageRef = useRef<(text: string) => void>();
  const startListeningRef = useRef<() => void>();
  const [interim, setInterim] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastFailedMessage, setLastFailedMessage] = useState<string | null>(null);

  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [voiceReplies, setVoiceReplies] = useState(true);
  const [uiMode, setUiMode] = useState<'text' | 'hands-free'>('text');
  const uiModeRef = useRef(uiMode);
  uiModeRef.current = uiMode;

  const [statusMessage, setStatusMessage] = useState('');
  const [prescription, setPrescription] = useState<PresentedPrescription | null>(null);
  const [prescriptionLang, setPrescriptionLang] = useState<string>('en');
  const [loadingPrescription, setLoadingPrescription] = useState(false);
  const [reviewRejected, setReviewRejected] = useState(false);

  const [recommendAppointment, setRecommendAppointment] = useState(false);
  const [recommendedSpecialty, setRecommendedSpecialty] = useState<string | null>(null);
  const [appointment, setAppointment] = useState<Appointment | null>(null);
  const [bookingAppointment, setBookingAppointment] = useState(false);

  const [clinicInfo, setClinicInfo] = useState<ClinicInfo | null>(null);

  const speechRef = useRef<SpeechInput | null>(null);
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);

  const speechSupported = useMemo(() => isRecognitionSupported(), []);
  const synthesisSupported = useMemo(() => isSynthesisSupported(), []);

  /* ------------------------------------------------------------- bootstrap */

  useEffect(() => {
    const savedProfile = window.localStorage.getItem('patient_profile');
    if (savedProfile) {
      try {
        const profile = JSON.parse(savedProfile) as PatientProfile;
        setPatientProfile(profile);
        setPhase('dashboard');
        loadHistory(profile.patient_id);
      } catch (e) {
        setPhase('auth');
      }
    } else {
      setPhase('auth');
    }

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

  /* ------------------------------------------------------------- auth & dashboard */

  const loadHistory = async (patientId: string) => {
    try {
      const hist = await api.getPatientHistory(patientId);
      setHistory(hist);
    } catch (err) {
      console.error('Failed to load history', err);
    }
  };

  const handleCreateProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!authName.trim() || !authAge.trim()) {
      setAuthError('Name and age are required.');
      return;
    }
    setLoadingAuth(true);
    setAuthError('');
    try {
      const profile = await api.createProfile(authName.trim(), authAge.trim());
      window.localStorage.setItem('patient_profile', JSON.stringify(profile));
      setPatientProfile(profile);
      setPhase('dashboard');
      loadHistory(profile.patient_id);
    } catch (err) {
      setAuthError(err instanceof ApiError ? err.message : 'Failed to create profile');
    } finally {
      setLoadingAuth(false);
    }
  };

  const handleLogout = () => {
    window.localStorage.removeItem('patient_profile');
    setPatientProfile(null);
    setHistory([]);
    setPhase('auth');
  };

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

  const beginSession = useCallback(async (selectedLang: Language) => {
    setLanguage(selectedLang);
    setError(null);
    setPhase('conversation');

    try {
      const session = await api.startSession(
        selectedLang.code,
        patientProfile?.name || 'Patient',
        patientProfile?.patient_id
      );
      setSessionId(session.session_id);
      setPatientStatus(session.status);
      setPrescriptionLang(selectedLang.code);

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
  }, [patientProfile]);

  const submitMessage = useCallback(
    async (text: string) => {
      if (!sessionId || !text.trim() || sending) return;

      const value = text.trim();
      setDraftWithRef('');
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

        // Stop thinking spinner so text displays immediately
        setSending(false);

        if (response.is_complete) {
          stopSpeaking();
          if (response.triage_status === 'URGENT') {
            setPhase('waiting');
          } else {
            await api.assess(sessionId);
            await api.handOff(sessionId);
            setPhase('waiting');
          }
          return;
        }

        // Only speak aloud and auto-listen if in hands-free mode!
        if (uiModeRef.current === 'hands-free' && language) {
          setSpeaking(true);
          await speak(response.ai_response, language.speech_tag).catch(() => {});
          setSpeaking(false);
          if (startListeningRef.current) {
             startListeningRef.current();
          }
        }
      } catch (err) {
        setLastFailedMessage(value);
        setMessages((prev) => prev.slice(0, -1));
        setError(
          err instanceof Error
            ? err.message
            : 'Something went wrong. Your information is saved — please try again.',
        );
        setSending(false);
      }
    },
    [sessionId, sending, language, setDraftWithRef],
  );
  
  submitMessageRef.current = submitMessage;

  const handleEndCall = useCallback(async () => {
    if (!sessionId) return;
    stopSpeaking();
    speechRef.current?.stop();
    setListening(false);
    setSpeaking(false);
    setSending(true);
    try {
      if (risk !== 'URGENT') {
        await api.assess(sessionId).catch(() => {});
        await api.handOff(sessionId).catch(() => {});
      }
      setPhase('waiting');
    } catch (err) {
      setError('Failed to send case to doctor. Please try again.');
    } finally {
      setSending(false);
    }
  }, [sessionId, risk]);

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

  const startListening = useCallback(() => {
    if (!language || !speechRef.current || speechRef.current.listening) return;
    stopSpeaking();

    const started = speechRef.current.start(language.speech_tag, {
      onResult: (transcript, isFinal) => {
        if (isFinal) {
          setDraftWithRef((prev) => (prev ? `${prev} ${transcript}` : transcript));
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
        if (draftRef.current.trim() && submitMessageRef.current) {
          submitMessageRef.current(draftRef.current);
        }
      },
    });
    if (started) setListening(true);
  }, [language, setDraftWithRef]);

  startListeningRef.current = startListening;

  const toggleListening = useCallback(() => {
    if (speaking) {
      stopSpeaking();
      setSpeaking(false);
    }
    if (listening) {
      speechRef.current?.stop();
    } else {
      startListening();
    }
  }, [listening, speaking, startListening]);

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

  if (phase === 'auth') {
    return (
      <AuthPhase
        name={authName}
        age={authAge}
        onNameChange={setAuthName}
        onAgeChange={setAuthAge}
        onSubmit={handleCreateProfile}
        loading={loadingAuth}
        error={authError}
      />
    );
  }

  if (phase === 'dashboard') {
    return (
      <DashboardPhase
        profile={patientProfile!}
        history={history}
        onStart={() => setPhase('language')}
        onLogout={handleLogout}
        onViewPrescription={(prescriptionId) => {
          setLoadingPrescription(true);
          api.getPrescription(prescriptionId, 'en').then((p) => {
            setPrescription(p);
            setPrescriptionLang('en');
            setPhase('prescription');
          }).finally(() => setLoadingPrescription(false));
        }}
      />
    );
  }

  if (phase === 'language') {
    return (
      <LanguageChooser
        languages={languages}
        error={error}
        onSelect={beginSession}
        onBack={() => setPhase(patientProfile ? 'dashboard' : 'auth')}
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
          <VoiceInterface
            uiMode={uiMode}
            onModeChange={(mode: 'text' | 'hands-free') => {
              setUiMode(mode);
              if (mode === 'hands-free') {
                if (speechSupported && !listening) {
                  toggleListening();
                }
              } else {
                if (listening) {
                  toggleListening();
                }
              }
            }}
            messages={messages}
            sending={sending}
            speaking={speaking}
            error={error}
            lastFailedMessage={lastFailedMessage}
            language={language}
            onRetry={() => lastFailedMessage && submitMessage(lastFailedMessage)}
            draft={draft}
            interim={interim}
            listening={listening}
            speechSupported={speechSupported}
            synthesisSupported={synthesisSupported}
            voiceReplies={voiceReplies}
            onToggleVoiceReplies={() => {
              stopSpeaking();
              setSpeaking(false);
              setVoiceReplies((v) => !v);
            }}
            onToggleListening={toggleListening}
            onSubmit={(text?: string) => submitMessage(text !== undefined ? text : draft)}
            onDraftChange={setDraft}
            onEndCall={handleEndCall}
            onClearError={() => setError(null)}
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
            patientId={clinicalState?.patient_id ?? patientProfile?.patient_id ?? null}
            patientProfile={patientProfile}
          />
        )}
      </main>
    </div>
  );
}

/* ====================================================================== */

function LanguageChooser({
  languages,
  error,
  onSelect,
  onBack,
}: {
  languages: Language[];
  error: string | null;
  onSelect: (language: Language) => void;
  onBack: () => void;
}) {
  return (
    <div className="flex min-h-screen flex-col bg-paper">
      <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center px-5 py-16">
        <button onClick={onBack} className="mb-4 self-start text-sm font-medium text-ink-muted hover:text-ink">
          ← Back
        </button>
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

function VoiceInterface({ uiMode, onModeChange, ...props }: any) {
  if (uiMode === 'hands-free') {
    return <HandsFreeModeView {...props} onExit={() => onModeChange('text')} />;
  }
  
  return <TextModeView {...props} onEnterHandsFree={() => onModeChange('hands-free')} />;
}

function TextModeView({
  messages,
  sending,
  error,
  lastFailedMessage,
  language,
  onRetry,
  draft,
  interim,
  listening,
  speechSupported,
  synthesisSupported,
  voiceReplies,
  onToggleVoiceReplies,
  onToggleListening,
  onSubmit,
  onDraftChange,
  onEnterHandsFree,
  onClearError,
}: any) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const wasSending = useRef(false);

  // Auto-scroll to bottom. Deliberately not keyed on `draft`: typing no longer
  // renders anything in the transcript, so scrolling on each keystroke was
  // just jitter.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, interim, sending]);

  // The input is disabled while a message is in flight, and disabling an
  // element blurs it — so after every send the patient had to click back into
  // the box to type the next answer. Restore focus on the send->settled edge
  // specifically, rather than on mount, so that arriving on the page does not
  // pop up the keyboard on a phone before the patient has chosen to type.
  useEffect(() => {
    if (wasSending.current && !sending && !listening) {
      inputRef.current?.focus();
    }
    wasSending.current = sending;
  }, [sending, listening]);

  return (
    <div className="flex flex-col min-h-[75vh] relative pb-24">
      {/* Standard Chat Scrollable Container */}
      <div className="flex flex-col gap-4 px-6 sm:px-12 pt-10 pb-16 w-full max-w-4xl mx-auto h-[60vh] overflow-y-auto no-scrollbar">
        {messages.length === 0 && !draft && !interim && !sending && (
          <div className="flex justify-center mt-10">
            <div className="bg-surface border border-rule rounded-2xl px-6 py-4 shadow-sm text-center">
              <h2 className="text-xl font-bold text-ink mb-1">Ready to begin.</h2>
              <p className="text-ink-muted">How can I help you today?</p>
            </div>
          </div>
        )}

        {messages.map((m: any, idx: number) => {
           const isAi = m.sender === 'ai';
           return (
             <div key={idx} className={cx("flex flex-col animate-rise", isAi ? "items-start" : "items-end")}>
               <div className={cx(
                 "max-w-[85%] sm:max-w-[75%] rounded-2xl px-5 py-3 shadow-sm",
                 isAi ? "bg-white border border-rule text-ink rounded-tl-none" : "bg-accent text-white rounded-tr-none"
               )}>
                 <div className="flex items-center gap-2 mb-1">
                   <p className={cx("text-[11px] font-semibold uppercase tracking-wider", isAi ? "text-ink-faint" : "text-white/80")}>
                     {isAi ? 'Assistant' : 'You'}
                   </p>
                   {isAi && (
                     <button
                       type="button"
                       onClick={() => speak(m.text, language?.speech_tag)}
                       className={cx("p-1 transition-colors rounded-full ml-auto", isAi ? "text-ink-faint hover:text-accent" : "text-white/70 hover:text-white")}
                       title="Listen to this message"
                     >
                       <VolumeIcon className="w-3 h-3" />
                     </button>
                   )}
                 </div>
                 <p className="text-base leading-relaxed whitespace-pre-wrap break-words">
                   {m.text}
                 </p>
               </div>
             </div>
           );
        })}

        {/* Live speech only. What is being typed is already on screen in the
            input, so echoing `draft` here showed every keystroke twice and
            pushed a half-written sentence into the transcript before the
            patient had decided to send it. Interim speech has nowhere else to
            appear, so it still previews here. */}
        {interim && (
          <div className="flex flex-col items-end animate-rise">
             <div className="max-w-[85%] sm:max-w-[75%] rounded-2xl px-5 py-3 shadow-sm bg-accent/80 text-white rounded-tr-none">
               <p className="text-[11px] font-semibold uppercase tracking-wider text-white/80 mb-1">You</p>
               <p className="text-base leading-relaxed whitespace-pre-wrap italic opacity-90 break-words">
                 {interim}
               </p>
             </div>
          </div>
        )}

        {sending && (
           <div className="flex flex-col items-start animate-rise">
              <div className="bg-white border border-rule rounded-2xl rounded-tl-none px-5 py-4 shadow-sm flex items-center gap-3">
                 <Spinner />
                 <span className="text-sm font-medium text-ink-muted">Thinking...</span>
              </div>
           </div>
        )}
        <div ref={scrollRef} className="h-4 shrink-0" />
      </div>

      {/* Bottom Input Bar */}
      <div className="fixed bottom-0 left-0 right-0 p-4 bg-gradient-to-t from-paper via-paper/90 to-transparent flex justify-center pb-8">
        <div className="w-full max-w-3xl flex items-center gap-3">
           <button 
             onClick={onEnterHandsFree} 
             className="p-3 bg-surface border border-rule rounded-full shadow-sm hover:bg-surface-sunken hover:border-ink transition-colors group relative flex-shrink-0" 
             aria-label="Enter hands-free voice mode"
             title="Switch to Hands-Free Voice Mode"
           >
              <HeadphonesIcon className="w-6 h-6 text-ink-muted group-hover:text-ink transition-colors" />
           </button>
           
           <div className="flex-1 flex items-center gap-2 border border-rule rounded-full bg-surface py-2 px-4 shadow-sm focus-within:border-accent focus-within:shadow-md transition-all">
              <input
                ref={inputRef}
                type="text"
                className="flex-1 bg-transparent outline-none text-ink placeholder-ink-faint text-base w-full"
                placeholder="Type your response..."
                value={draft}
                onChange={(e) => onDraftChange(e.target.value)}
                onKeyDown={(e) => {
                   if (e.key === 'Enter' && e.currentTarget.value.trim()) {
                      onSubmit(e.currentTarget.value);
                   }
                }}
                disabled={sending || listening}
              />
              {speechSupported && (
                <button 
                  onClick={onToggleListening}
                  className={cx("p-2 rounded-full transition-colors flex-shrink-0", listening ? "bg-risk-urgent text-white shadow-risk-urgent/40 shadow-lg animate-pulse" : "text-accent hover:bg-accent-soft")}
                  title={listening ? "Stop listening" : "Start speaking"}
                >
                  {listening ? <MicActiveIcon className="w-5 h-5" /> : <MicIcon className="w-5 h-5" />}
                </button>
              )}
              <button 
                onClick={() => { if (draft.trim()) onSubmit(draft); }}
                disabled={sending || listening || !draft.trim()}
                className="text-accent font-semibold disabled:opacity-50 px-2 flex-shrink-0 hover:text-accent-hover transition-colors"
              >
                Send
              </button>
           </div>
        </div>
      </div>

      {error && (
        <div className="fixed top-20 left-1/2 -translate-x-1/2 z-50 w-full max-w-xl px-4">
          <ErrorNotice message={error} onRetry={lastFailedMessage ? onRetry : undefined} onClose={onClearError} />
        </div>
      )}
    </div>
  );
}

function HandsFreeModeView({
  messages,
  sending,
  speaking,
  error,
  lastFailedMessage,
  language,
  onRetry,
  draft,
  interim,
  listening,
  speechSupported,
  onSubmit,
  onToggleListening,
  onExit,
  onClearError,
}: any) {
  const scrollRef = useRef<HTMLDivElement>(null);
  
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, draft, interim, sending]);

  return (
    <div className="flex flex-col items-center justify-center min-h-[75vh] text-center space-y-6 relative pb-10">
      <div className="absolute top-0 right-4 z-10 pt-4">
        <button onClick={onExit} className="text-[13px] font-semibold text-ink-muted underline underline-offset-2 hover:text-ink transition-colors bg-paper/80 px-2 py-1 rounded">
          Exit hands-free mode
        </button>
      </div>

      {/* Spotify Lyrics Style Scrollable Container for Hands-Free */}
      <div className="flex flex-col gap-10 px-6 sm:px-12 pt-16 pb-6 w-full max-w-4xl mx-auto h-[50vh] overflow-y-auto no-scrollbar">
        {messages.length === 0 && !draft && !interim && !sending && (
          <div className="opacity-50 mt-10">
             <p className="text-[12px] font-semibold text-ink-faint uppercase tracking-wider mb-2">Assistant</p>
             <h2 className="text-3xl sm:text-4xl font-bold text-ink leading-tight tracking-tight">Ready to begin.</h2>
          </div>
        )}

        {messages.map((m: any, idx: number) => {
           const isLast = idx === messages.length - 1 && !draft && !interim && !sending;
           return (
             <div key={idx} className={cx("transition-all duration-700 ease-out", isLast ? "opacity-100 scale-100" : "opacity-30 scale-95 blur-[0.5px]")}>
               <p className={cx("text-[12px] font-semibold uppercase tracking-wider mb-2", m.sender === 'ai' ? 'text-ink-faint' : 'text-accent/70')}>
                 {m.sender === 'ai' ? 'Assistant' : 'You'}
               </p>
               <h2 lang={m.sender === 'ai' ? language?.code : undefined} className="text-3xl sm:text-4xl font-bold text-ink leading-tight tracking-tight break-words">
                 "{m.text}"
               </h2>
             </div>
           );
        })}

        {/* Live speech only. Hands-free has no text input, so including
            `draft` here surfaced whatever half-typed sentence was left behind
            in text mode as a full-size quote on entering this view. */}
        {interim && (
          <div className="opacity-100 scale-100 animate-rise transition-all duration-700">
             <p className="text-[12px] font-semibold text-accent/70 uppercase tracking-wider mb-2">You</p>
             <h2 className="text-3xl sm:text-4xl font-bold text-ink-muted italic leading-tight tracking-tight break-words">
               "{interim}"
             </h2>
          </div>
        )}

        {sending && (
           <div className="opacity-100 scale-100 flex flex-col items-center justify-center gap-4 py-6 animate-pulse transition-all duration-700">
              <Spinner />
              <span className="text-xl font-bold text-ink-muted tracking-tight">Assistant is thinking...</span>
           </div>
        )}
        <div ref={scrollRef} className="h-10 shrink-0" />
      </div>

      {/* Microphone Control */}
      <div className="flex flex-col items-center gap-6">
        {speechSupported ? (
          <button
            onClick={() => {
               if (listening) {
                 onToggleListening();
                 setTimeout(() => onSubmit(), 200);
               } else {
                 onToggleListening();
               }
            }}
            className={cx(
              "flex items-center justify-center rounded-full w-24 h-24 sm:w-32 sm:h-32 transition-all duration-300 shadow-xl border-4",
              listening 
                ? "bg-risk-urgent border-risk-urgent text-white animate-pulse shadow-risk-urgent/40 scale-110" 
                : speaking
                ? "bg-amber-500 border-amber-500 text-white animate-pulse scale-105 shadow-amber-500/40"
                : "bg-accent border-accent text-white hover:scale-105 hover:shadow-accent/40"
            )}
            aria-label={listening ? 'Stop listening' : speaking ? 'Stop speaking' : 'Start speaking'}
          >
             {listening ? <MicActiveIcon className="w-10 h-10 sm:w-12 sm:h-12" /> : <MicIcon className="w-10 h-10 sm:w-12 sm:h-12" />}
          </button>
        ) : (
          <p className="text-ink-muted text-sm border border-rule px-4 py-2 bg-surface">Microphone not supported on this device.</p>
        )}
        
        <div className="flex flex-col items-center gap-2 h-10 mt-4">
          {listening ? (
             <p className="text-risk-urgent font-semibold text-lg animate-pulse uppercase tracking-wider">Listening...</p>
          ) : speaking ? (
             <p className="text-amber-600 font-semibold text-lg animate-pulse uppercase tracking-wider">Assistant speaking (Tap to interrupt)</p>
          ) : sending ? (
             <p className="text-accent font-semibold text-lg uppercase tracking-wider">Assistant is thinking...</p>
          ) : (
             <p className="text-accent font-semibold text-lg uppercase tracking-wider">Tap mic to speak</p>
          )}
        </div>
      </div>

      {error && (
        <ErrorNotice message={error} onRetry={lastFailedMessage ? onRetry : undefined} onClose={onClearError} />
      )}
    </div>
  );
}

function HeadphonesIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 18v-6a9 9 0 0 1 18 0v6"></path>
      <path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"></path>
    </svg>
  );
}

function VolumeIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
      <path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path>
      <path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path>
    </svg>
  );
}

function MicIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden="true">
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" strokeLinecap="round" />
    </svg>
  );
}

function MicActiveIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="2" width="6" height="12" rx="3"></rect>
      <path d="M5 10v2a7 7 0 0 0 14 0v-2"></path>
      <line x1="12" y1="19" x2="12" y2="22"></line>
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
  patientProfile,
}: {
  prescription: PresentedPrescription;
  clinicInfo: ClinicInfo | null;
  patientId: string | null;
  patientProfile?: PatientProfile | null;
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
          {patientProfile?.name && (
            <p className="text-base font-bold mb-1">
              Patient Name: <span className="font-semibold">{patientProfile.name}</span>
              {patientProfile.age ? ` (${patientProfile.age} yrs)` : ''}
            </p>
          )}
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
  patientProfile,
}: {
  prescription: PresentedPrescription;
  languages: Language[];
  activeLanguage: string;
  loading: boolean;
  onLanguageChange: (code: string) => void;
  clinicInfo: ClinicInfo | null;
  patientId: string | null;
  patientProfile?: PatientProfile | null;
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
        patientProfile={patientProfile}
      />

      <div className="animate-rise space-y-8 print:hidden">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="label-meta text-risk-low">Approved by your doctor</p>
            <h1 className="mt-3 text-title font-semibold text-ink">Your prescription</h1>
            {patientProfile?.name && (
              <p className="mt-1 text-base font-bold text-ink">
                Patient: {patientProfile.name} {patientProfile.age ? `(Age: ${patientProfile.age})` : ''}
              </p>
            )}
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

function AuthPhase({
  name,
  age,
  onNameChange,
  onAgeChange,
  onSubmit,
  loading,
  error,
}: {
  name: string;
  age: string;
  onNameChange: (v: string) => void;
  onAgeChange: (v: string) => void;
  onSubmit: (e: React.FormEvent) => void;
  loading: boolean;
  error: string;
}) {
  return (
    <div className="flex min-h-screen flex-col bg-paper px-5 py-16 items-center justify-center">
      <div className="w-full max-w-md">
        <h1 className="text-display font-semibold text-ink mb-2">Welcome</h1>
        <p className="text-body text-ink-muted mb-8">
          Please enter your details to create a profile and save your consultations.
        </p>
        
        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-ink mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => onNameChange(e.target.value)}
              className="w-full rounded border border-rule bg-surface px-3 py-2 text-ink placeholder:text-ink-faint focus:border-ink focus:outline-none"
              placeholder="e.g. John Doe"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-ink mb-1">Age</label>
            <input
              type="number"
              value={age}
              onChange={(e) => onAgeChange(e.target.value)}
              className="w-full rounded border border-rule bg-surface px-3 py-2 text-ink placeholder:text-ink-faint focus:border-ink focus:outline-none"
              placeholder="e.g. 35"
            />
          </div>
          
          {error && <div className="text-red-500 text-sm">{error}</div>}
          
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded bg-ink px-4 py-2.5 text-center font-medium text-paper transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {loading ? 'Continuing...' : 'Continue'}
          </button>
        </form>
      </div>
    </div>
  );
}

function DashboardPhase({
  profile,
  history,
  onStart,
  onLogout,
  onViewPrescription,
}: {
  profile: PatientProfile;
  history: PatientHistoryItem[];
  onStart: () => void;
  onLogout: () => void;
  onViewPrescription: (id: string) => void;
}) {
  return (
    <div className="min-h-screen bg-paper pb-32">
      <header className="sticky top-0 z-10 border-b border-rule bg-paper/95 px-5 py-4 backdrop-blur">
        <div className="mx-auto flex max-w-2xl items-center justify-between">
          <div>
            <h1 className="font-semibold text-ink">{profile.name}</h1>
            <p className="text-xs text-ink-muted">Age: {profile.age}</p>
          </div>
          <button onClick={onLogout} className="text-sm text-ink-muted hover:text-ink transition-colors">
            Logout
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-2xl px-5 pt-8">
        <div className="mb-10">
          <button
            onClick={onStart}
            className="w-full rounded-lg bg-ink py-4 text-center text-lg font-medium text-paper shadow-sm transition-opacity hover:opacity-90 active:opacity-100"
          >
            Start New Consultation
          </button>
        </div>

        <SectionTitle>Past Consultations</SectionTitle>
        {history.length === 0 ? (
          <p className="mt-4 text-ink-muted">No past consultations found.</p>
        ) : (
          <div className="mt-4 space-y-3">
            {history.map((item) => (
              <div key={item.case_id} className="rounded-lg border border-rule bg-surface p-4">
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="font-medium text-ink line-clamp-1">{item.chief_complaint || 'No complaint specified'}</h3>
                    <p className="mt-1 text-sm text-ink-muted">
                      {new Date(item.timestamp).toLocaleDateString()} at {new Date(item.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}
                    </p>
                  </div>
                  <RiskBadge risk={item.triage_status} size="sm" />
                </div>
                
                {item.has_prescription && item.prescription_id && (
                  <div className="mt-4">
                    <button
                      onClick={() => onViewPrescription(item.prescription_id!)}
                      className="text-sm font-medium text-accent hover:underline"
                    >
                      View Prescription
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
