'use client';

/**
 * Live face-to-face consultation. One shared device, used together by doctor
 * and patient in the room — not two devices synced over a network, so there
 * is no websocket here, just request/response per utterance.
 *
 * Turn-taking is manual (push-to-talk), not automatic language detection:
 * browser SpeechRecognition only listens in one fixed language at a time.
 * SpeechInput.start() already listens for exactly one utterance and stops
 * itself (continuous: false), so "press, speak, it submits" falls out of the
 * existing wrapper for free — no separate silence-timer logic needed here.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import {
  ApiError,
  api,
  type CaseDetail,
  type ConsultationTurn,
  type Language,
  type LiveConsultation,
} from '@/lib/api';
import { SpeechInput, isRecognitionSupported, isSynthesisSupported, speak, stopSpeaking } from '@/lib/speech';
import { ErrorNotice, SectionTitle, Spinner, cx } from '@/components/ui/clinical';

type ActiveSpeaker = 'doctor' | 'patient' | null;

export default function ConsultationPage() {
  const router = useRouter();
  const params = useParams<{ caseId: string }>();
  const caseId = params.caseId;

  const [ready, setReady] = useState(false);
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [languages, setLanguages] = useState<Language[]>([]);
  const [consultation, setConsultation] = useState<LiveConsultation | null>(null);
  const [patientLangCode, setPatientLangCode] = useState<string>('en');

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ending, setEnding] = useState(false);

  const [activeSpeaker, setActiveSpeaker] = useState<ActiveSpeaker>(null);
  const [interim, setInterim] = useState('');
  const [voiceWarning, setVoiceWarning] = useState<string | null>(null);

  const speechRef = useRef<SpeechInput | null>(null);
  const finalTextRef = useRef('');
  const speechSupported = useMemo(() => isRecognitionSupported(), []);
  const synthesisSupported = useMemo(() => isSynthesisSupported(), []);

  const englishLang = languages.find((l) => l.code === 'en');
  const patientLang = languages.find((l) => l.code === patientLangCode) || englishLang;

  /* ------------------------------------------------------------- auth gate */

  useEffect(() => {
    if (!api.isDoctorAuthenticated()) {
      router.replace('/doctor/login');
      return;
    }
    setReady(true);
  }, [router]);

  /* ------------------------------------------------------------- bootstrap */

  useEffect(() => {
    if (!ready || !caseId) return;
    speechRef.current = new SpeechInput();

    (async () => {
      try {
        const [caseDetail, langs] = await Promise.all([
          api.getCaseDetail(caseId),
          api.listLanguages(),
        ]);
        setDetail(caseDetail);
        setLanguages(langs);
        setPatientLangCode(caseDetail.case.preferred_language || 'en');

        const started = await api.startConsultation(caseId);
        setConsultation(started);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Could not open this consultation.');
      } finally {
        setLoading(false);
      }
    })();

    return () => {
      speechRef.current?.stop();
      stopSpeaking();
    };
  }, [ready, caseId]);

  /* ------------------------------------------------------------ push-to-talk */

  const stopListening = useCallback(() => {
    speechRef.current?.stop();
    setActiveSpeaker(null);
    setInterim('');
  }, []);

  const submitTurn = useCallback(
    async (speaker: 'doctor' | 'patient', text: string) => {
      if (!consultation || !englishLang || !patientLang || !text.trim()) return;
      const sourceLang = speaker === 'doctor' ? englishLang.code : patientLang.code;
      const targetLang = speaker === 'doctor' ? patientLang.code : englishLang.code;
      const targetTag = speaker === 'doctor' ? patientLang.speech_tag : englishLang.speech_tag;

      try {
        const turn = await api.submitConsultationTurn(consultation.consultation_id, {
          speaker,
          text,
          sourceLang,
          targetLang,
        });
        setConsultation((prev) => (prev ? { ...prev, turns: [...prev.turns, turn] } : prev));
        const spoken = await speak(turn.translated_text, targetTag);
        setVoiceWarning(
          spoken
            ? null
            : `No spoken voice is installed on this device for ${targetTag} — the translation above is text-only.`,
        );
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Could not translate that turn.');
      }
    },
    [consultation, englishLang, patientLang],
  );

  const toggleSpeaker = useCallback(
    (speaker: 'doctor' | 'patient') => {
      if (!speechRef.current || !englishLang || !patientLang) return;

      if (activeSpeaker === speaker) {
        stopListening();
        return;
      }

      stopSpeaking();
      speechRef.current.stop();
      finalTextRef.current = '';
      setInterim('');
      setVoiceWarning(null);

      const languageTag = speaker === 'doctor' ? englishLang.speech_tag : patientLang.speech_tag;

      const started = speechRef.current.start(languageTag, {
        onResult: (transcript, isFinal) => {
          if (isFinal) {
            finalTextRef.current = finalTextRef.current
              ? `${finalTextRef.current} ${transcript}`
              : transcript;
            setInterim('');
          } else {
            setInterim(transcript);
          }
        },
        onError: (message) => {
          setError(message);
          setActiveSpeaker(null);
          setInterim('');
        },
        onEnd: () => {
          setActiveSpeaker(null);
          setInterim('');
          const text = finalTextRef.current.trim();
          finalTextRef.current = '';
          if (text) void submitTurn(speaker, text);
        },
      });

      if (started) setActiveSpeaker(speaker);
    },
    [activeSpeaker, englishLang, patientLang, stopListening, submitTurn],
  );

  const endConsultation = useCallback(async () => {
    if (!consultation) return;
    stopListening();
    setEnding(true);
    setError(null);
    try {
      const ended = await api.endConsultation(consultation.consultation_id);
      setConsultation(ended);
      // The draft-or-blocked outcome is only known after the backend
      // processes the finished transcript, which happens inside
      // endConsultation on the server. `detail` was fetched once at page
      // load and is stale by now — refetch so the completed-state message
      // below reflects what actually happened, not the pre-consultation state.
      setDetail(await api.getCaseDetail(caseId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not close the consultation.');
    } finally {
      setEnding(false);
    }
  }, [consultation, stopListening, caseId]);

  if (!ready) return null;

  return (
    <div className="min-h-screen bg-paper">
      <header className="sticky top-0 z-10 border-b border-rule bg-paper/95 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-4 px-6 py-3">
          <div className="flex items-baseline gap-4">
            <Link href="/doctor" className="text-[13px] text-ink-muted underline underline-offset-2">
              ← Queue
            </Link>
            <span className="label-meta">Face-to-face consultation</span>
          </div>
          {detail && (
            <span className="font-mono text-[12px] text-ink-faint">{detail.case.case_id}</span>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-8">
        {loading && (
          <div className="py-10">
            <Spinner label="Opening consultation" />
          </div>
        )}

        {error && (
          <div className="mb-6">
            <ErrorNotice message={error} />
          </div>
        )}

        {!loading && detail && consultation && (
          <>
            <section className="border-b border-rule pb-6">
              <h1 className="text-title font-semibold text-ink">
                {detail.case.chief_complaint || detail.case.symptoms[0] || 'Unspecified complaint'}
              </h1>
              <p className="mt-2 max-w-reading text-[14px] leading-relaxed text-ink-muted">
                {detail.case.summary_en || 'No prior intake summary recorded.'}
              </p>

              <label className="mt-4 inline-block">
                <span className="label-meta">Patient's language</span>
                <select
                  value={patientLangCode}
                  onChange={(e) => setPatientLangCode(e.target.value)}
                  disabled={consultation.status !== 'IN_PROGRESS' || consultation.turns.length > 0}
                  className="field mt-1"
                >
                  {languages.map((l) => (
                    <option key={l.code} value={l.code}>{l.native_name}</option>
                  ))}
                </select>
              </label>
            </section>

            {consultation.status === 'IN_PROGRESS' && (
              <section className="border-b border-rule py-6">
                {!speechSupported && (
                  <p className="mb-4 text-[13px] text-risk-urgent">
                    Voice input is not supported in this browser. This screen needs Chrome or Edge.
                  </p>
                )}

                <div className="grid gap-4 sm:grid-cols-2">
                  <button
                    type="button"
                    onClick={() => toggleSpeaker('doctor')}
                    disabled={!speechSupported}
                    aria-pressed={activeSpeaker === 'doctor'}
                    className={cx(
                      'border px-4 py-5 text-left transition-colors disabled:opacity-50',
                      activeSpeaker === 'doctor'
                        ? 'border-accent bg-accent-soft'
                        : 'border-rule-strong bg-surface hover:bg-surface-sunken',
                    )}
                  >
                    <span className="label-meta">
                      {activeSpeaker === 'doctor' ? 'Listening…' : 'Press to speak'}
                    </span>
                    <p className="mt-1 text-[15px] font-medium text-ink">Doctor speaking (English)</p>
                    {activeSpeaker === 'doctor' && interim && (
                      <p className="mt-1 text-[13px] text-ink-faint">{interim}</p>
                    )}
                  </button>

                  <button
                    type="button"
                    onClick={() => toggleSpeaker('patient')}
                    disabled={!speechSupported || !patientLang}
                    aria-pressed={activeSpeaker === 'patient'}
                    className={cx(
                      'border px-4 py-5 text-left transition-colors disabled:opacity-50',
                      activeSpeaker === 'patient'
                        ? 'border-accent bg-accent-soft'
                        : 'border-rule-strong bg-surface hover:bg-surface-sunken',
                    )}
                  >
                    <span className="label-meta">
                      {activeSpeaker === 'patient' ? 'Listening…' : 'Press to speak'}
                    </span>
                    <p className="mt-1 text-[15px] font-medium text-ink">
                      Patient speaking ({patientLang?.native_name ?? '…'})
                    </p>
                    {activeSpeaker === 'patient' && interim && (
                      <p className="mt-1 text-[13px] text-ink-faint">{interim}</p>
                    )}
                  </button>
                </div>

                {!synthesisSupported && (
                  <p className="mt-3 text-[12px] text-ink-faint">
                    Spoken playback is not supported in this browser — translations will still
                    appear below as text.
                  </p>
                )}

                {voiceWarning && (
                  <p className="mt-3 text-[12px] text-risk-uncertain">{voiceWarning}</p>
                )}

                <button
                  type="button"
                  onClick={endConsultation}
                  disabled={ending || consultation.turns.length === 0}
                  className="btn-primary mt-6"
                  title={consultation.turns.length === 0 ? 'Record at least one turn first' : undefined}
                >
                  {ending ? 'Generating draft prescription…' : 'End consultation & generate draft prescription'}
                </button>
              </section>
            )}

            {consultation.status === 'COMPLETED' && (
              <section className="border-b border-rule py-6">
                <SectionTitle note="English · Internal record">Encounter note</SectionTitle>
                <p className="mt-3 max-w-reading text-[14px] leading-relaxed text-ink">
                  {consultation.report_en}
                </p>
                {detail?.prescription_draft ? (
                  <p className="mt-4 max-w-reading text-[13px] leading-relaxed text-ink-muted">
                    Saved to this case's record — never shown to the patient. A draft prescription
                    has been generated from this consultation; review and release it from the case
                    queue.
                  </p>
                ) : (
                  <p className="mt-4 max-w-reading text-[13px] leading-relaxed text-ink-muted">
                    Saved to this case's record — never shown to the patient. No draft was
                    generated
                    {detail?.draft_block_reason ? ` (${detail.draft_block_reason.toLowerCase().replace(/_/g, ' ')})` : ''}
                    . You can still write a prescription directly from the case queue using
                    &ldquo;Modify&rdquo;.
                  </p>
                )}
                <Link href="/doctor" className="btn-secondary mt-3 inline-block">
                  Back to case queue
                </Link>
              </section>
            )}

            <section className="py-6">
              <SectionTitle note={`${consultation.turns.length} turns`}>Transcript</SectionTitle>
              {consultation.turns.length === 0 ? (
                <p className="mt-4 text-[13px] text-ink-faint">
                  Nothing recorded yet. Press a button above to start.
                </p>
              ) : (
                <ul className="mt-4 space-y-4">
                  {consultation.turns.map((turn, index) => (
                    <TurnRow key={index} turn={turn} />
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  );
}

function TurnRow({ turn }: { turn: ConsultationTurn }) {
  const isDoctor = turn.speaker === 'doctor';
  return (
    <li className={cx('flex', isDoctor ? 'justify-start' : 'justify-end')}>
      <div className={cx('max-w-[85%]', isDoctor ? 'text-left' : 'text-right')}>
        <span className="label-meta">{isDoctor ? 'Doctor' : 'Patient'}</span>
        <p className="mt-1 whitespace-pre-wrap border-l-2 border-rule bg-surface px-3 py-2 text-[14px] leading-relaxed text-ink">
          {turn.original_text}
        </p>
        <p className="mt-1 whitespace-pre-wrap px-3 text-[13px] leading-relaxed text-ink-muted">
          → {turn.translated_text}
        </p>
      </div>
    </li>
  );
}
