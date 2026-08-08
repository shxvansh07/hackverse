'use client';

/**
 * Doctor dashboard. Desktop-first, two panes: live queue on the left, case
 * detail on the right.
 *
 * Information hierarchy is summary-first — the English clinical summary and
 * the safety signals sit above the fold; the transcript and grounding detail
 * are below. The four decision actions are pinned so they never require a
 * scroll to reach.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  ApiError,
  api,
  type CaseDetail,
  type DecisionType,
  type Medication,
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
      options: { notes?: string; medications?: Medication[]; instructions?: string } = {},
    ) => {
      if (!selectedId) return;
      try {
        const result = await api.submitDecision(selectedId, decision, {
          notes: options.notes,
          modifiedMedications: options.medications,
          modifiedInstructions: options.instructions,
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

                    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-faint">
                      <span>{item.patient_id}</span>
                      {item.duration && <span>{item.duration}</span>}
                      <span>{new Date(item.created_at).toLocaleTimeString()}</span>
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
            <CaseReview key={detail.case.case_id} detail={detail} onDecide={submitDecision} />
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
}: {
  detail: CaseDetail;
  onDecide: (
    decision: DecisionType,
    options?: { notes?: string; medications?: Medication[]; instructions?: string },
  ) => Promise<void>;
}) {
  const { case: kase, prescription_draft: draft, safety_signal: safety, grounding } = detail;

  const [notes, setNotes] = useState('');
  const [editing, setEditing] = useState(false);
  const [medications, setMedications] = useState<Medication[]>(draft?.medications ?? []);
  const [instructions, setInstructions] = useState(draft?.instructions ?? '');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setMedications(draft?.medications ?? []);
    setInstructions(draft?.instructions ?? '');
    setEditing(false);
    setNotes('');
  }, [draft?.prescription_id]);

  const decided =
    kase.review_status === 'APPROVED' ||
    kase.review_status === 'MODIFIED' ||
    kase.review_status === 'REJECTED';

  const run = async (decision: DecisionType) => {
    setBusy(true);
    try {
      if (decision === 'MODIFY') {
        await onDecide('MODIFY', { notes, medications, instructions });
        setEditing(false);
      } else {
        await onDecide(decision, { notes });
      }
    } finally {
      setBusy(false);
    }
  };

  const updateMedication = (index: number, patch: Partial<Medication>) =>
    setMedications((prev) => prev.map((m, i) => (i === index ? { ...m, ...patch } : m)));

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

        <h2 className="mt-4 text-title font-semibold text-ink">
          {kase.chief_complaint || kase.symptoms[0] || 'Unspecified complaint'}
        </h2>

        <p className="mt-4 max-w-reading text-body leading-relaxed text-ink">
          {kase.summary_en || 'No summary generated.'}
        </p>
      </section>

      {/* ------------------------------------------------------ safety first */}
      {kase.triage_status === 'URGENT' && (
        <div className="border-b border-rule bg-risk-urgent-soft px-8 py-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-risk-urgent">
            Urgent — prescription workflow blocked
          </p>
          <p className="mt-1 max-w-reading text-[13px] leading-relaxed text-ink-muted">
            The safety layer detected a red flag and did not generate a draft. You can still
            prescribe directly using Modify.
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

      {/* -------------------------------------------------------- transcript */}
      <section className="px-8 py-6">
        <SectionTitle note={`${kase.transcript.length} turns`}>
          Original conversation
        </SectionTitle>
        <ul className="mt-4 space-y-3">
          {kase.transcript.map((message, index) => (
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
      </section>

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
                  disabled={busy || medications.length === 0}
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
