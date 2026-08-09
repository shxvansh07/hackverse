'use client';

/**
 * Doctor dashboard. Desktop-first, two panes: live queue on the left, case
 * detail on the right.
 *
 * Information hierarchy is summary-first — the English clinical summary and
 * the safety signals sit above the fold; the transcript and grounding detail
 * are below. The decision actions are pinned so they never require a scroll
 * to reach.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  ApiError,
  api,
  type CaseDetail,
  type DecisionType,
  type Medication,
  type SimilarCase,
  type TriageCase,
} from '@/lib/api';
import {
  AiDraftBanner,
  ErrorNotice,
  Field,
  Empty,
  ReviewBadge,
  RiskBadge,
  SectionTitle,
  Spinner,
  cx,
} from '@/components/ui/clinical';

const REFERRAL_SPECIALTIES = [
  'Cardiology',
  'ENT',
  'Neurology',
  'Orthopedics',
  'Gastroenterology',
  'Dermatology',
  'General Surgery',
  'Pulmonology',
  'Psychiatry',
  'Obstetrics & Gynecology',
];

export default function DoctorDashboard() {
  const router = useRouter();

  const [ready, setReady] = useState(false);
  const [cases, setCases] = useState<TriageCase[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CaseDetail | null>(null);

  const [loadingQueue, setLoadingQueue] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [riskFilter, setRiskFilter] = useState<string>('');

  const socketRef = useRef<WebSocket | null>(null);

  /* ------------------------------------------------------------- auth gate */

  useEffect(() => {
    if (!api.isDoctorAuthenticated()) {
      router.replace('/doctor/login');
      return;
    }
    setReady(true);
  }, [router]);

  /* ----------------------------------------------------------------- queue */

  const loadQueue = useCallback(async () => {
    try {
      const list = await api.getDoctorCases(riskFilter || undefined);
      setCases(list);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace('/doctor/login');
        return;
      }
      setError(err instanceof ApiError ? err.message : 'Could not load the case queue.');
    } finally {
      setLoadingQueue(false);
    }
  }, [riskFilter, router]);

  useEffect(() => {
    if (ready) loadQueue();
  }, [ready, loadQueue]);

  /* ------------------------------------------------------------- websocket */

  useEffect(() => {
    if (!ready) return;

    const url = api.doctorSocketUrl();
    if (!url) return;

    let closed = false;
    let retryTimer: ReturnType<typeof setTimeout>;

    const connect = () => {
      if (closed) return;
      const socket = new WebSocket(url);
      socketRef.current = socket;

      socket.onopen = () => setLive(true);

      socket.onmessage = (event) => {
        try {
          const { event: type, data } = JSON.parse(event.data);
          if (type === 'CONNECTED') return;

          // Refresh the queue rather than patching it locally, so filters and
          // ordering stay authoritative on the server.
          loadQueue();

          if (type === 'URGENT_ALERT') {
            setFlash(`Urgent case received — ${data.chief_complaint || data.case_id}`);
          }
          // Keep an open case in sync when a decision lands elsewhere.
          setSelectedId((current) => {
            if (current && current === data.case_id) void refreshDetail(current);
            return current;
          });
        } catch {
          /* ignore malformed frames */
        }
      };

      socket.onclose = () => {
        setLive(false);
        if (!closed) retryTimer = setTimeout(connect, 3000);
      };

      socket.onerror = () => socket.close();
    };

    connect();
    return () => {
      closed = true;
      clearTimeout(retryTimer);
      socketRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, loadQueue]);

  // Heartbeat keeps intermediaries from dropping an idle socket.
  useEffect(() => {
    const timer = setInterval(() => {
      if (socketRef.current?.readyState === WebSocket.OPEN) socketRef.current.send('ping');
    }, 25000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!flash) return;
    const timer = setTimeout(() => setFlash(null), 6000);
    return () => clearTimeout(timer);
  }, [flash]);

  /* ---------------------------------------------------------------- detail */

  const refreshDetail = useCallback(async (caseId: string) => {
    try {
      setDetail(await api.getCaseDetail(caseId));
    } catch {
      /* keep the previous view rather than blanking mid-review */
    }
  }, []);

  const addNote = useCallback(
    async (text: string) => {
      if (!selectedId) return;
      await api.addCaseNote(selectedId, text);
      await refreshDetail(selectedId);
    },
    [selectedId, refreshDetail],
  );

  const openCase = useCallback(async (caseId: string) => {
    setSelectedId(caseId);
    setLoadingDetail(true);
    setError(null);
    try {
      setDetail(await api.getCaseDetail(caseId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load this case.');
      setDetail(null);
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  const submitDecision = useCallback(
    async (
      decision: DecisionType,
      options: {
        notes?: string;
        medications?: Medication[];
        instructions?: string;
        referralSpecialty?: string;
        referralNotes?: string;
        offlineAppointmentTime?: string;
        offlineClinicLocation?: string;
      } = {},
    ) => {
      if (!selectedId) return;
      try {
        const result = await api.submitDecision(selectedId, decision, {
          notes: options.notes,
          modifiedMedications: options.medications,
          modifiedInstructions: options.instructions,
          referralSpecialty: options.referralSpecialty,
          referralNotes: options.referralNotes,
          offlineAppointmentTime: options.offlineAppointmentTime,
          offlineClinicLocation: options.offlineClinicLocation,
        });
        setFlash(result.message);
        await Promise.all([loadQueue(), refreshDetail(selectedId)]);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Could not record that decision.');
      }
    },
    [selectedId, loadQueue, refreshDetail],
  );

  if (!ready) return null;

  return (
    <div className="flex h-screen flex-col bg-paper">
      <header className="flex items-center justify-between border-b border-rule px-6 py-3">
        <div className="flex items-baseline gap-4">
          <h1 className="text-[15px] font-semibold tracking-tight text-ink">
            Clinical Assistant
          </h1>
          <span className="label-meta">Doctor review</span>
        </div>

        <div className="flex items-center gap-5">
          <span className="flex items-center gap-2 text-[12px] text-ink-muted">
            <span
              className={cx(
                'h-1.5 w-1.5 rounded-full',
                live ? 'bg-risk-low animate-pulse-dot' : 'bg-ink-faint',
              )}
              aria-hidden
            />
            {live ? 'Live' : 'Reconnecting'}
          </span>
          <span className="text-[13px] text-ink-muted">{api.doctorName()}</span>
          <button
            onClick={() => {
              api.doctorLogout();
              router.replace('/doctor/login');
            }}
            className="text-[13px] text-ink-muted underline underline-offset-2 hover:text-ink"
          >
            Sign out
          </button>
        </div>
      </header>

      {flash && (
        <div className="border-b border-accent/20 bg-accent-soft px-6 py-2">
          <p className="text-[13px] text-accent">{flash}</p>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-[360px] shrink-0 flex-col border-r border-rule">
          <div className="border-b border-rule px-5 py-3">
            <div className="flex items-baseline justify-between">
              <h2 className="label-meta">Queue</h2>
              <span className="text-[12px] text-ink-faint">{cases.length}</span>
            </div>
            <div className="mt-3 flex gap-px bg-rule">
              {[
                { value: '', label: 'All' },
                { value: 'URGENT', label: 'Urgent' },
                { value: 'UNCERTAIN', label: 'Uncertain' },
                { value: 'LOW_RISK', label: 'Low' },
              ].map((option) => (
                <button
                  key={option.value}
                  onClick={() => setRiskFilter(option.value)}
                  className={cx(
                    'flex-1 px-2 py-1.5 text-[12px] transition-colors',
                    riskFilter === option.value
                      ? 'bg-ink text-white'
                      : 'bg-surface text-ink-muted hover:bg-surface-sunken',
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="scroll-hairline min-h-0 flex-1 overflow-y-auto">
            {loadingQueue && (
              <div className="px-5 py-6">
                <Spinner label="Loading queue" />
              </div>
            )}

            {!loadingQueue && cases.length === 0 && (
              <p className="px-5 py-6 text-[13px] leading-relaxed text-ink-faint">
                No cases yet. New patient hand-offs appear here automatically.
              </p>
            )}

            <ul>
              {cases.map((item) => (
                <li key={item.case_id}>
                  <button
                    onClick={() => openCase(item.case_id)}
                    className={cx(
                      'w-full border-b border-rule px-5 py-4 text-left transition-colors',
                      selectedId === item.case_id
                        ? 'bg-surface-sunken'
                        : 'hover:bg-surface-sunken/60',
                      item.triage_status === 'URGENT' && 'border-l-2 border-l-risk-urgent',
                    )}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="font-mono text-[11px] text-ink-faint">
                        {item.case_id}
                      </span>
                      <RiskBadge risk={item.triage_status} size="sm" />
                    </div>

                    <p className="mt-2 text-[15px] font-medium leading-snug text-ink">
                      {item.chief_complaint || item.symptoms[0] || 'Unspecified complaint'}
                    </p>

                    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-muted">
                      <span className="font-semibold text-ink">{item.patient_name || item.patient_id}</span>
                      {item.age && <span>Age: {item.age}</span>}
                      {item.duration && <span>{item.duration}</span>}
                      <span>{new Date(item.created_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}</span>
                    </div>

                    <div className="mt-2 flex items-center gap-2">
                      <ReviewBadge status={item.review_status} />
                      {item.prescription_id ? (
                        <span className="text-[11px] uppercase tracking-[0.1em] text-draft">
                          Draft ready
                        </span>
                      ) : (
                        <span className="text-[11px] uppercase tracking-[0.1em] text-ink-faint">
                          No draft
                        </span>
                      )}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </aside>

        <main className="scroll-hairline min-w-0 flex-1 overflow-y-auto">
          {error && (
            <div className="px-8 pt-6">
              <ErrorNotice message={error} onRetry={loadQueue} />
            </div>
          )}

          {loadingDetail && (
            <div className="px-8 py-10">
              <Spinner label="Loading case" />
            </div>
          )}

          {!loadingDetail && !detail && (
            <div className="flex h-full items-center justify-center px-8">
              <p className="max-w-reading text-center text-[15px] leading-relaxed text-ink-faint">
                Select a case to review its clinical summary, safety signals and any AI draft.
              </p>
            </div>
          )}

          {!loadingDetail && detail && (
            <CaseReview
              key={detail.case.case_id}
              detail={detail}
              onDecide={submitDecision}
              onAddNote={addNote}
              onOpenCase={openCase}
            />
          )}
        </main>
      </div>
    </div>
  );
}

/* ====================================================================== */

function CaseReview({
  detail,
  onDecide,
  onAddNote,
  onOpenCase,
}: {
  detail: CaseDetail;
  onDecide: (
    decision: DecisionType,
    options?: {
      notes?: string;
      medications?: Medication[];
      instructions?: string;
      referralSpecialty?: string;
      referralNotes?: string;
      offlineAppointmentTime?: string;
      offlineClinicLocation?: string;
    },
  ) => Promise<void>;
  onAddNote: (text: string) => Promise<void>;
  onOpenCase: (caseId: string) => void;
}) {
  const router = useRouter();
  const { case: kase, prescription_draft: draft, safety_signal: safety, grounding, appointment, referral, consultation, record } = detail;

  const [notes, setNotes] = useState('');
  const [editing, setEditing] = useState(false);
  const [medications, setMedications] = useState<Medication[]>(draft?.medications ?? []);
  const [instructions, setInstructions] = useState(draft?.instructions ?? '');
  const [busy, setBusy] = useState(false);
  const [newNoteText, setNewNoteText] = useState('');
  const [addingNote, setAddingNote] = useState(false);

  const [referralSpecialty, setReferralSpecialty] = useState(
    kase.recommended_specialty || REFERRAL_SPECIALTIES[0],
  );
  const [referralNotes, setReferralNotes] = useState('');
  const [offlineTime, setOfflineTime] = useState('');
  const [offlineLocation, setOfflineLocation] = useState('Main Hospital OPD Clinic, Room 102');

  useEffect(() => {
    setMedications(draft?.medications ?? []);
    setInstructions(draft?.instructions ?? '');
    setEditing(false);
    setNotes('');
  }, [draft?.prescription_id]);

  // Mirrors the backend's Medication validation (min_length=1 after trim on
  // name/dosage/frequency/duration) — catch a blank row here, before the
  // API call, rather than only after a 422 comes back.
  const medicationsInvalid =
    medications.length === 0 ||
    medications.some(
      (m) =>
        !m.name.trim() || !m.dosage.trim() || !m.frequency.trim() || !m.duration.trim(),
    );

  const decided =
    kase.review_status === 'APPROVED' ||
    kase.review_status === 'MODIFIED' ||
    kase.review_status === 'REJECTED' ||
    kase.review_status === 'REFERRED' ||
    kase.review_status === 'OFFLINE_SCHEDULED';

  const run = async (decision: DecisionType) => {
    setBusy(true);
    try {
      if (decision === 'MODIFY') {
        await onDecide('MODIFY', { notes, medications, instructions });
        setEditing(false);
      } else if (decision === 'REFERRAL') {
        await onDecide('REFERRAL', { notes, referralSpecialty, referralNotes });
      } else if (decision === 'OFFLINE_APPOINTMENT') {
        await onDecide('OFFLINE_APPOINTMENT', {
          notes,
          offlineAppointmentTime: offlineTime,
          offlineClinicLocation: offlineLocation,
        });
      } else {
        await onDecide(decision, { notes });
      }
    } finally {
      setBusy(false);
    }
  };

  const updateMedication = (index: number, patch: Partial<Medication>) =>
    setMedications((prev) => prev.map((m, i) => (i === index ? { ...m, ...patch } : m)));

  const submitNote = async () => {
    const text = newNoteText.trim();
    if (!text) return;
    setAddingNote(true);
    try {
      await onAddNote(text);
      setNewNoteText('');
    } finally {
      setAddingNote(false);
    }
  };

  return (
    <div className="pb-40">
      {/* ---------------------------------------------------- summary first */}
      <section className="border-b border-rule px-8 py-6">
        <div className="flex flex-wrap items-center gap-3">
          <span className="font-mono text-[12px] text-ink-faint">{kase.case_id}</span>
          <RiskBadge risk={kase.triage_status} />
          <ReviewBadge status={kase.review_status} />
          <span className="text-[12px] text-ink-faint">
            {kase.patient_id} · {kase.preferred_language.toUpperCase()} ·{' '}
            {new Date(kase.created_at).toLocaleString()}
          </span>
        </div>

        <div className="mt-4 rounded-lg border border-rule bg-surface p-4">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <span className="text-[12px] font-semibold uppercase tracking-wider text-ink-faint">Patient Information</span>
              <h3 className="text-xl font-bold text-ink">
                {(detail.patient_name && detail.patient_name !== 'Patient')
                  ? detail.patient_name
                  : (kase.patient_name && kase.patient_name !== 'Patient'
                    ? kase.patient_name
                    : (detail.patient_profile?.name || 'Patient'))}
              </h3>
              <p className="text-sm text-ink-muted">
                Patient ID: <span className="font-mono text-ink">{kase.patient_id}</span>
                {' · Age: '}
                <span className="font-medium text-ink">
                  {kase.age || detail.patient_profile?.age || 'Not specified'}
                </span>
                {' · Language: '}
                <span className="font-medium text-ink uppercase">{kase.preferred_language}</span>
              </p>
            </div>
            <div className="flex items-center gap-2">
              <RiskBadge risk={kase.triage_status} />
              <ReviewBadge status={kase.review_status} />
            </div>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-4 border-t border-rule pt-3 text-sm sm:grid-cols-4">
            <div>
              <span className="block text-[11px] uppercase tracking-wider text-ink-faint">Medical History</span>
              <span className="font-medium text-ink">{kase.medical_history.length > 0 ? kase.medical_history.join(', ') : 'None reported'}</span>
            </div>
            <div>
              <span className="block text-[11px] uppercase tracking-wider text-ink-faint">Known Allergies</span>
              <span className="font-medium text-ink">{kase.allergies.length > 0 ? kase.allergies.join(', ') : (kase.allergies_confirmed ? 'No known allergies' : 'Not confirmed')}</span>
            </div>
            <div>
              <span className="block text-[11px] uppercase tracking-wider text-ink-faint">Current Medications</span>
              <span className="font-medium text-ink">{kase.medications.length > 0 ? kase.medications.join(', ') : 'None'}</span>
            </div>
            <div>
              <span className="block text-[11px] uppercase tracking-wider text-ink-faint">Symptoms & Duration</span>
              <span className="font-medium text-ink">{kase.symptoms.join(', ') || 'N/A'} {kase.duration ? `(${kase.duration})` : ''}</span>
            </div>
          </div>
        </div>

        <h2 className="mt-6 text-title font-semibold text-ink">
          Chief Complaint: {kase.chief_complaint || kase.symptoms[0] || 'Unspecified complaint'}
        </h2>

        <p className="mt-3 max-w-reading text-body leading-relaxed text-ink">
          {kase.summary_en || 'No summary generated.'}
        </p>

        {kase.recommended_specialty && (
          <p className="mt-3 text-[13px] text-ink-muted">
            Deterministic engine suggests: <span className="text-ink">{kase.recommended_specialty}</span>
          </p>
        )}
      </section>

      {/* --------------------------------------------------- patient history */}
      {detail.patient_history && (
        <section className="border-b border-rule px-8 py-6">
          <SectionTitle
            note={`${detail.patient_history.visit_count} prior visit${
              detail.patient_history.visit_count === 1 ? '' : 's'
            }`}
          >
            Patient history
          </SectionTitle>

          {detail.patient_history.carried_forward && (
            <p className="mt-3 text-[13px] text-ink-muted">
              Allergy and medical history carried forward from a previous visit — not re-asked
              this visit. Review before relying on it.
            </p>
          )}

          {detail.patient_history.highlight && (
            <p className="mt-3 max-w-reading text-[14px] leading-relaxed text-ink">
              {detail.patient_history.highlight}
            </p>
          )}

          <div className="mt-4 divide-y divide-rule border border-rule">
            {detail.patient_history.recent_cases.map((visit) => (
              <button
                key={visit.case_id}
                type="button"
                onClick={() => onOpenCase(visit.case_id)}
                className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-sunken"
              >
                <div>
                  <p className="text-[14px] text-ink">
                    {visit.chief_complaint || 'Unspecified complaint'}
                  </p>
                  <p className="mt-0.5 text-[12px] text-ink-faint">
                    {new Date(visit.created_at).toLocaleDateString()}
                  </p>
                </div>
                <RiskBadge risk={visit.triage_status} size="sm" />
              </button>
            ))}
          </div>
        </section>
      )}

      {/* ------------------------------------------------------ safety first */}
      {kase.triage_status === 'URGENT' && (
        <div className="border-b border-rule bg-risk-urgent-soft px-8 py-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-risk-urgent">
            Urgent — prescription workflow blocked
          </p>
          <p className="mt-1 max-w-reading text-[13px] leading-relaxed text-ink-muted">
            The safety layer detected a red flag and did not generate a draft. You can still
            prescribe directly using Modify, or refer/schedule below.
          </p>
        </div>
      )}

      <section className="border-b border-rule px-8 py-6">
        <SectionTitle note="Deterministic">Safety signals</SectionTitle>

        <div className="mt-4 grid gap-6 md:grid-cols-2">
          <div>
            <h3 className="label-meta">Red flags</h3>
            {kase.red_flags.length ? (
              <ul className="mt-2 space-y-1">
                {kase.red_flags.map((flag) => (
                  <li key={flag} className="flex items-center gap-2 text-[14px] text-risk-urgent">
                    <span className="h-1 w-1 rounded-full bg-risk-urgent" aria-hidden />
                    {flag}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-[14px] text-ink-faint">None detected</p>
            )}
          </div>

          <div>
            <h3 className="label-meta">Missing information</h3>
            {kase.missing_information.length ? (
              <ul className="mt-2 space-y-1">
                {kase.missing_information.map((field) => (
                  <li key={field} className="text-[14px] text-risk-uncertain">
                    {field.replace(/_/g, ' ')}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-[14px] text-ink-faint">Intake complete</p>
            )}
          </div>
        </div>

        {safety?.reasons?.length ? (
          <div className="mt-5 border-t border-rule pt-4">
            <h3 className="label-meta">Routing rationale</h3>
            <ul className="mt-2 space-y-1">
              {safety.reasons.map((reason, i) => (
                <li key={i} className="max-w-reading text-[13px] leading-relaxed text-ink-muted">
                  {reason}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      {/* ------------------------------------------------------------ case notes */}
      <section className="border-b border-rule px-8 py-6">
        <SectionTitle note="Doctor to doctor">Case notes</SectionTitle>

        {kase.case_notes.length > 0 ? (
          <ul className="mt-4 space-y-4">
            {kase.case_notes.map((note) => (
              <li key={note.note_id} className="border-l-2 border-rule pl-4">
                <p className="max-w-reading text-[14px] leading-relaxed text-ink">{note.text}</p>
                <p className="mt-1 text-[12px] text-ink-faint">
                  {note.doctor_name} · {new Date(note.created_at).toLocaleString()}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-[13px] text-ink-faint">
            No notes yet. Leave context here for whoever reviews this case next.
          </p>
        )}

        <div className="mt-4 flex items-start gap-3">
          <textarea
            value={newNoteText}
            onChange={(e) => setNewNoteText(e.target.value)}
            placeholder="Add a note for whoever reviews this case next…"
            rows={2}
            className="field flex-1"
          />
          <button
            onClick={submitNote}
            disabled={addingNote || !newNoteText.trim()}
            className="btn-secondary shrink-0"
          >
            {addingNote ? 'Adding…' : 'Add note'}
          </button>
        </div>
      </section>

      {/* ------------------------------------------------------- clinical detail */}
      <section className="border-b border-rule px-8 py-6">
        <SectionTitle>Clinical detail</SectionTitle>
        <dl className="mt-2 grid gap-x-10 md:grid-cols-2">
          <Field label="Symptoms">
            {kase.symptoms.length ? kase.symptoms.join(', ') : <Empty>Not recorded</Empty>}
          </Field>
          <Field label="Associated symptoms">
            {kase.associated_symptoms.length ? (
              kase.associated_symptoms.join(', ')
            ) : (
              <Empty>None reported</Empty>
            )}
          </Field>
          <Field label="Duration">{kase.duration || <Empty>Not recorded</Empty>}</Field>
          <Field label="Severity">{kase.severity || <Empty>Not stated</Empty>}</Field>
          <Field label="Medical history">
            {kase.medical_history.length ? (
              kase.medical_history.join('; ')
            ) : kase.history_confirmed ? (
              'None reported'
            ) : (
              <Empty>Not established</Empty>
            )}
          </Field>
          <Field label="Current medications">
            {kase.medications.length ? kase.medications.join('; ') : <Empty>None reported</Empty>}
          </Field>
          <Field label="Allergies">
            {kase.allergies.length ? (
              <span className="font-medium text-risk-uncertain">{kase.allergies.join('; ')}</span>
            ) : kase.allergies_confirmed ? (
              'None reported'
            ) : (
              <Empty>Not established</Empty>
            )}
          </Field>
          <Field label="Age">{kase.age || <Empty>Not stated</Empty>}</Field>
        </dl>
      </section>

      {/* ------------------------------------------------------------- draft */}
      <section className="border-b border-rule px-8 py-6">
        <SectionTitle note={draft?.icd10_code || undefined}>Prescription</SectionTitle>

        <div className="mt-4">
          {draft && draft.is_ai_draft && <AiDraftBanner />}
          {!draft && <AiDraftBanner blocked />}
        </div>

        {detail.draft_blocked && detail.draft_block_reason === 'ALLERGY_CONFLICT' && (
          <div className="mt-3 border border-risk-urgent/30 bg-risk-urgent-soft px-4 py-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-risk-urgent">
              Allergy conflict
            </p>
            <ul className="mt-1 space-y-0.5">
              {(grounding?.conflicts as string[] | undefined)?.map((c, i) => (
                <li key={i} className="text-[13px] text-ink-muted">
                  {c}
                </li>
              ))}
            </ul>
          </div>
        )}

        {draft && (
          <>
            {draft.matched_condition && (
              <p className="mt-4 text-[13px] text-ink-muted">
                Matched protocol:{' '}
                <span className="text-ink">{draft.matched_condition}</span>
                {draft.icd10_code && (
                  <span className="font-mono text-ink-faint"> · {draft.icd10_code}</span>
                )}
              </p>
            )}

            <ul className="mt-4 space-y-px bg-rule">
              {(editing ? medications : draft.medications).map((med, index) => (
                <li key={index} className="bg-surface px-4 py-4">
                  {editing ? (
                    <div className="grid gap-2 sm:grid-cols-2">
                      {(
                        [
                          ['name', 'Medication'],
                          ['dosage', 'Dosage'],
                          ['frequency', 'Frequency'],
                          ['duration', 'Duration'],
                        ] as const
                      ).map(([key, label]) => (
                        <label key={key} className="block">
                          <span className="label-meta">{label}</span>
                          <input
                            value={med[key]}
                            onChange={(e) => updateMedication(index, { [key]: e.target.value })}
                            className="field mt-1"
                          />
                        </label>
                      ))}
                      <label className="block sm:col-span-2">
                        <span className="label-meta">Instructions</span>
                        <input
                          value={med.instructions}
                          onChange={(e) =>
                            updateMedication(index, { instructions: e.target.value })
                          }
                          className="field mt-1"
                        />
                      </label>
                      <button
                        onClick={() =>
                          setMedications((prev) => prev.filter((_, i) => i !== index))
                        }
                        className="justify-self-start text-[12px] text-risk-urgent underline underline-offset-2"
                      >
                        Remove
                      </button>
                    </div>
                  ) : (
                    <>
                      <div className="flex flex-wrap items-baseline gap-x-3">
                        <span className="text-[15px] font-semibold text-ink">{med.name}</span>
                        <span className="font-mono text-[14px] text-ink">{med.dosage}</span>
                      </div>
                      <p className="mt-1 text-[13px] text-ink-muted">
                        {med.frequency} · {med.duration}
                      </p>
                      {med.instructions && (
                        <p className="mt-1 text-[13px] leading-relaxed text-ink-muted">
                          {med.instructions}
                        </p>
                      )}
                    </>
                  )}
                </li>
              ))}

              {editing && (
                <li className="bg-surface px-4 py-3">
                  <button
                    onClick={() =>
                      setMedications((prev) => [
                        ...prev,
                        { name: '', dosage: '', frequency: '', duration: '', instructions: '' },
                      ])
                    }
                    className="text-[13px] text-accent underline underline-offset-2"
                  >
                    Add medication
                  </button>
                </li>
              )}
            </ul>

            {editing && medicationsInvalid && (
              <p className="mt-3 text-[13px] text-risk-urgent">
                {medications.length === 0
                  ? 'Add at least one medication before releasing this prescription.'
                  : 'Every medication needs a name, dosage, frequency and duration — a blank field will be rejected.'}
              </p>
            )}

            <div className="mt-4">
              <span className="label-meta">General instructions</span>
              {editing ? (
                <textarea
                  value={instructions}
                  onChange={(e) => setInstructions(e.target.value)}
                  rows={3}
                  className="field mt-1"
                />
              ) : (
                <p className="mt-1 max-w-reading text-[14px] leading-relaxed text-ink">
                  {draft.instructions || <Empty>None</Empty>}
                </p>
              )}
            </div>

            {draft.rationale && (
              <div className="mt-5 border-l-2 border-rule pl-4">
                <span className="label-meta">Rationale</span>
                <p className="mt-1 max-w-reading text-[13px] leading-relaxed text-ink-muted">
                  {draft.rationale}
                </p>
                {draft.grounding_sources.length > 0 && (
                  <p className="mt-2 font-mono text-[11px] text-ink-faint">
                    {draft.grounding_sources.join(' · ')}
                  </p>
                )}
              </div>
            )}
          </>
        )}

        {!draft && !editing && (
          <button
            onClick={() => {
              setEditing(true);
              setMedications([
                { name: '', dosage: '', frequency: '', duration: '', instructions: '' },
              ]);
            }}
            className="btn-secondary mt-4"
          >
            Write a prescription
          </button>
        )}
      </section>

      {/* ------------------------------------------------- referral & offline */}
      <section className="border-b border-rule px-8 py-6">
        <SectionTitle note="Manual">Referral & in-person appointment</SectionTitle>

        {referral && (
          <div className="mt-4 border border-rule bg-surface px-4 py-3">
            <p className="label-meta">Specialist referral issued</p>
            <p className="mt-1 text-[14px] text-ink">
              {referral.specialty}
              {referral.referral_notes && ` — ${referral.referral_notes}`}
            </p>
          </div>
        )}

        {appointment && (
          <div className="mt-4 border border-rule bg-surface px-4 py-3">
            <p className="label-meta">Appointment on record</p>
            <p className="mt-1 text-[14px] text-ink">
              {appointment.type} · {appointment.slot_time} · {appointment.clinic_location}
            </p>
          </div>
        )}

        <div className="mt-4 flex items-center justify-between border border-rule bg-surface px-4 py-3">
          <div>
            <p className="label-meta">Face-to-face consultation</p>
            <p className="mt-1 text-[13px] text-ink-muted">
              {consultation?.status === 'COMPLETED'
                ? 'Completed — draft prescription generated for review.'
                : consultation?.status === 'IN_PROGRESS'
                  ? 'In progress — resume on the doctor\'s device.'
                  : 'For when the patient is here in person: real-time interpreted, scribed, ends in a draft prescription for your review.'}
            </p>
          </div>
          <button
            onClick={() => router.push(`/doctor/consultation/${kase.case_id}`)}
            className="btn-secondary shrink-0"
          >
            {consultation?.status === 'IN_PROGRESS' ? 'Resume' : consultation?.status === 'COMPLETED' ? 'View' : 'Start consultation'}
          </button>
        </div>

        <div className="mt-4 grid gap-6 md:grid-cols-2">
          <div>
            <h3 className="label-meta">Refer to specialist</h3>
            <label className="mt-2 block">
              <span className="label-meta">Specialty</span>
              <select
                value={referralSpecialty}
                onChange={(e) => setReferralSpecialty(e.target.value)}
                className="field mt-1"
              >
                {REFERRAL_SPECIALTIES.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </label>
            <label className="mt-2 block">
              <span className="label-meta">Clinical reason</span>
              <input
                value={referralNotes}
                onChange={(e) => setReferralNotes(e.target.value)}
                placeholder="Optional"
                className="field mt-1"
              />
            </label>
            <button
              onClick={() => run('REFERRAL')}
              disabled={busy}
              className="btn-secondary mt-3"
            >
              Issue referral
            </button>
          </div>

          <div>
            <h3 className="label-meta">Schedule in-person appointment</h3>
            <label className="mt-2 block">
              <span className="label-meta">Date & time</span>
              <input
                value={offlineTime}
                onChange={(e) => setOfflineTime(e.target.value)}
                placeholder="e.g. 2026-08-10 11:00 AM"
                className="field mt-1"
              />
            </label>
            <label className="mt-2 block">
              <span className="label-meta">Clinic location</span>
              <input
                value={offlineLocation}
                onChange={(e) => setOfflineLocation(e.target.value)}
                className="field mt-1"
              />
            </label>
            <button
              onClick={() => run('OFFLINE_APPOINTMENT')}
              disabled={busy}
              className="btn-secondary mt-3"
            >
              Schedule appointment
            </button>
          </div>
        </div>
      </section>

      {/* ----------------------------------------------------------- record */}
      <section className="px-8 py-6">
        <SectionTitle note="Kept for continuity of care and audit">Case record</SectionTitle>

        <div className="mt-4">
          <h3 className="label-meta">Initial conversation</h3>
          <ul className="mt-2 space-y-3">
            {record.initial_conversation.map((message, index) => (
              <li key={index} className="flex gap-3">
                <span className="label-meta w-16 shrink-0 pt-1">
                  {message.sender === 'patient' ? 'Patient' : 'AI'}
                </span>
                <p
                  className={cx(
                    'max-w-reading text-[14px] leading-relaxed',
                    message.sender === 'patient' ? 'text-ink' : 'text-ink-muted',
                  )}
                >
                  {message.text}
                </p>
              </li>
            ))}
          </ul>
        </div>

        {record.consultation_transcript.length > 0 && (
          <div className="mt-6 border-t border-rule pt-4">
            <h3 className="label-meta">Face-to-face consultation</h3>
            <ul className="mt-2 space-y-3">
              {record.consultation_transcript.map((turn, index) => (
                <li key={index} className="flex gap-3">
                  <span className="label-meta w-16 shrink-0 pt-1">
                    {turn.speaker === 'patient' ? 'Patient' : 'Doctor'}
                  </span>
                  <p className="max-w-reading text-[14px] leading-relaxed text-ink">
                    {turn.speaker === 'doctor' ? turn.original_text : turn.translated_text}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        )}

        {record.encounter_note && (
          <div className="mt-6 border-t border-rule pt-4">
            <h3 className="label-meta">Encounter note</h3>
            <p className="mt-2 max-w-reading text-[14px] leading-relaxed text-ink">
              {record.encounter_note}
            </p>
          </div>
        )}

        <div className="mt-6 border-t border-rule pt-4">
          <h3 className="label-meta">Prescription</h3>
          {record.prescription ? (
            <div className="mt-2">
              <ul className="space-y-1">
                {record.prescription.medications.map((med, index) => (
                  <li key={index} className="text-[14px] text-ink">
                    <span className="font-medium">{med.name}</span>{' '}
                    <span className="font-mono text-[13px]">{med.dosage}</span> · {med.frequency} ·{' '}
                    {med.duration}
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-[13px] text-ink-muted">
                {record.prescription.status} · {record.prescription.doctor_name || 'doctor'}
                {record.finalized_at && ` · ${new Date(record.finalized_at).toLocaleString()}`}
              </p>
            </div>
          ) : (
            <p className="mt-2 text-[13px] text-ink-faint">Not yet finalized.</p>
          )}
        </div>
      </section>

      {/* ---------------------------------------------------- similar cases */}
      {Array.isArray(grounding?.similar_cases) && grounding.similar_cases.length > 0 && (
        <section className="border-b border-rule px-8 py-6">
          <SectionTitle note="De-identified · other patients">Similar past cases</SectionTitle>
          <ul className="mt-4 space-y-3">
            {(grounding.similar_cases as SimilarCase[]).map((sc, index) => (
              <li key={index} className="border border-rule bg-surface px-4 py-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="text-[14px] font-medium text-ink">
                    {sc.chief_complaint || 'Unspecified complaint'} → {sc.diagnosis}
                  </p>
                  {sc.was_modified_from_ai_draft && (
                    <span className="text-[11px] uppercase tracking-[0.1em] text-draft">
                      Doctor modified the AI suggestion
                    </span>
                  )}
                </div>
                <p className="mt-1 text-[13px] text-ink-muted">
                  {sc.medications.join(', ') || 'No medication on record'}
                </p>
                <p className="mt-1 text-[12px] text-ink-faint">
                  {sc.doctor_name}
                  {sc.doctor_years_experience != null && ` · ${sc.doctor_years_experience} yrs`}
                  {sc.approved_at && ` · ${new Date(sc.approved_at).toLocaleDateString()}`}
                </p>
                {sc.doctor_notes && (
                  <p className="mt-2 text-[13px] italic leading-relaxed text-ink-muted">
                    “{sc.doctor_notes}”
                  </p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ------------------------------------------------ actions always visible */}
      <div className="fixed inset-x-0 bottom-0 left-[360px] border-t border-rule bg-paper/95 px-8 py-4 backdrop-blur">
        {decided && (
          <p className="mb-3 text-[12px] text-ink-muted">
            This case has been decided. Submitting again will replace the previous decision.
          </p>
        )}

        <div className="flex items-end gap-3">
          <label className="flex-1">
            <span className="label-meta">Doctor notes</span>
            <input
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Optional note recorded with your decision"
              className="field mt-1"
            />
          </label>

          <div className="flex shrink-0 gap-2">
            {editing ? (
              <>
                <button
                  onClick={() => setEditing(false)}
                  disabled={busy}
                  className="btn-secondary"
                >
                  Cancel
                </button>
                <button
                  onClick={() => run('MODIFY')}
                  disabled={busy || medicationsInvalid}
                  title={
                    medicationsInvalid
                      ? 'Every medication needs a name, dosage, frequency and duration before this can be released.'
                      : undefined
                  }
                  className="btn-primary"
                >
                  Save & release
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={() => run('NEEDS_REVIEW')}
                  disabled={busy}
                  className="btn-secondary"
                >
                  Needs review
                </button>
                <button onClick={() => run('REJECT')} disabled={busy} className="btn-danger">
                  Reject
                </button>
                <button
                  onClick={() => setEditing(true)}
                  disabled={busy}
                  className="btn-secondary"
                >
                  Modify
                </button>
                <button
                  onClick={() => run('APPROVE')}
                  disabled={busy || !draft || draft.medications.length === 0}
                  className="btn-primary"
                  title={!draft ? 'No draft to approve — use Modify to prescribe' : undefined}
                >
                  Approve
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
