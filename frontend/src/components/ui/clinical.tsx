/**
 * Shared clinical UI primitives.
 *
 * These exist so that safety-critical labelling is defined once. In
 * particular `AiDraftBanner` is the only place the "AI-generated draft"
 * warning is written — a second, subtly different version of that warning
 * elsewhere in the app would be a real hazard.
 */

import type { ReactNode } from 'react';
import type { PatientStatus, ReviewStatus, RiskState } from '@/lib/api';

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

/* ------------------------------------------------------------------ risk */

const RISK_STYLE: Record<RiskState, { label: string; className: string }> = {
  LOW_RISK: { label: 'Low risk', className: 'border-risk-low/30 bg-risk-low-soft text-risk-low' },
  UNCERTAIN: {
    label: 'Uncertain',
    className: 'border-risk-uncertain/30 bg-risk-uncertain-soft text-risk-uncertain',
  },
  URGENT: {
    label: 'Urgent',
    className: 'border-risk-urgent/40 bg-risk-urgent-soft text-risk-urgent',
  },
};

export function RiskBadge({ risk, size = 'md' }: { risk: RiskState; size?: 'sm' | 'md' }) {
  const style = RISK_STYLE[risk];
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1.5 border font-medium uppercase tracking-[0.1em]',
        size === 'sm' ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-1 text-[11px]',
        style.className,
      )}
    >
      {risk === 'URGENT' && (
        <span className="h-1.5 w-1.5 rounded-full bg-risk-urgent animate-pulse-dot" aria-hidden />
      )}
      {style.label}
    </span>
  );
}

/* --------------------------------------------------------------- statuses */

const REVIEW_LABEL: Record<ReviewStatus, string> = {
  NEW: 'New',
  IN_REVIEW: 'In review',
  NEEDS_REVIEW: 'Needs review',
  APPROVED: 'Approved',
  MODIFIED: 'Modified',
  REJECTED: 'Rejected',
  URGENT: 'Urgent',
  REFERRED: 'Referred',
  OFFLINE_SCHEDULED: 'In-person scheduled',
};

export function ReviewBadge({ status }: { status: ReviewStatus }) {
  const emphasised =
    status === 'APPROVED' ||
    status === 'MODIFIED' ||
    status === 'REFERRED' ||
    status === 'OFFLINE_SCHEDULED';
  return (
    <span
      className={cx(
        'inline-flex border px-2 py-1 text-[11px] font-medium uppercase tracking-[0.1em]',
        emphasised
          ? 'border-risk-low/30 bg-risk-low-soft text-risk-low'
          : 'border-rule bg-surface-sunken text-ink-muted',
      )}
    >
      {REVIEW_LABEL[status]}
    </span>
  );
}

export const PATIENT_STATUS_LABEL: Record<PatientStatus, string> = {
  COLLECTING_INFORMATION: 'Collecting information',
  ASSESSING: 'Assessing',
  WAITING_FOR_DOCTOR: 'Waiting for doctor',
  URGENT_ESCALATION: 'Urgent escalation',
  APPROVED: 'Approved',
  REVIEW_COMPLETE: 'Review complete',
};

/** Ordered progress rail shown to the patient. */
export function StatusRail({ current }: { current: PatientStatus }) {
  const steps: Array<{ key: PatientStatus; label: string }> = [
    { key: 'COLLECTING_INFORMATION', label: 'Your symptoms' },
    { key: 'ASSESSING', label: 'Safety check' },
    { key: 'WAITING_FOR_DOCTOR', label: 'Doctor review' },
    { key: 'APPROVED', label: 'Prescription' },
  ];

  // Escalated cases leave the normal rail entirely rather than showing
  // progress toward a prescription they will not receive.
  if (current === 'URGENT_ESCALATION') {
    return (
      <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.12em] text-risk-urgent">
        <span className="h-1.5 w-1.5 rounded-full bg-risk-urgent animate-pulse-dot" aria-hidden />
        Escalated for urgent care
      </div>
    );
  }

  const activeIndex = Math.max(
    0,
    steps.findIndex((s) => s.key === current || (current === 'REVIEW_COMPLETE' && s.key === 'APPROVED')),
  );

  return (
    <ol className="flex items-center gap-1" aria-label="Progress">
      {steps.map((step, index) => {
        const done = index < activeIndex;
        const active = index === activeIndex;
        return (
          <li key={step.key} className="flex flex-1 items-center gap-1">
            <div className="flex-1">
              <div
                className={cx(
                  'h-0.5 w-full transition-colors',
                  done ? 'bg-ink' : active ? 'bg-accent' : 'bg-rule',
                )}
              />
              <span
                className={cx(
                  'mt-1.5 block text-[10px] uppercase tracking-[0.1em]',
                  active ? 'text-ink' : done ? 'text-ink-muted' : 'text-ink-faint',
                )}
              >
                {step.label}
              </span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/* ------------------------------------------------------------- AI framing */

/**
 * The single canonical "this is not a prescription yet" warning.
 * Rendered wherever an unapproved draft is visible.
 */
export function AiDraftBanner({ blocked = false }: { blocked?: boolean }) {
  if (blocked) {
    return (
      <div className="border border-risk-urgent/30 bg-risk-urgent-soft px-4 py-3">
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-risk-urgent">
          No AI draft generated
        </p>
        <p className="mt-1 text-[13px] leading-relaxed text-ink-muted">
          The safety layer blocked automatic drafting for this case. Prescribe directly
          if clinically appropriate.
        </p>
      </div>
    );
  }

  return (
    <div className="border border-draft/30 bg-draft-soft px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-draft">
        AI-generated draft — requires doctor approval
      </p>
      <p className="mt-1 text-[13px] leading-relaxed text-ink-muted">
        Generated from the curated formulary and shown for review only. It is not a
        prescription and has not been sent to the patient.
      </p>
    </div>
  );
}

/* --------------------------------------------------------------- layout */

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="py-3">
      <dt className="label-meta">{label}</dt>
      <dd className="mt-1 text-[15px] leading-relaxed text-ink">{children}</dd>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <span className="text-ink-faint">{children}</span>;
}

export function SectionTitle({ children, note }: { children: ReactNode; note?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-rule pb-2">
      <h2 className="text-heading font-semibold text-ink">{children}</h2>
      {note && <span className="label-meta">{note}</span>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-ink-muted">
      <span className="flex gap-1" aria-hidden>
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-1.5 w-1.5 rounded-full bg-ink-faint animate-pulse-dot"
            style={{ animationDelay: `${i * 160}ms` }}
          />
        ))}
      </span>
      {label && <span className="text-[13px]">{label}</span>}
    </span>
  );
}

export function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex items-start justify-between gap-4 border border-risk-urgent/30 bg-risk-urgent-soft px-4 py-3"
    >
      <p className="text-[13px] leading-relaxed text-risk-urgent">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="shrink-0 text-[13px] font-medium text-risk-urgent underline underline-offset-2"
        >
          Retry
        </button>
      )}
    </div>
  );
}
