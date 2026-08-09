'use client';

/**
 * Anonymized symptom-trend view — aggregate counts only, never a case_id,
 * patient_id, or transcript. See PublicHealthService.compute_trends on the
 * backend, the only place this data is assembled.
 */

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { ApiError, api, type PublicHealthTrendsResponse, type SymptomTrend } from '@/lib/api';
import { ErrorNotice, SectionTitle, Spinner, cx } from '@/components/ui/clinical';

export default function PublicHealthTrendsPage() {
  const router = useRouter();

  const [ready, setReady] = useState(false);
  const [data, setData] = useState<PublicHealthTrendsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  /* ------------------------------------------------------------- auth gate */

  useEffect(() => {
    if (!api.isDoctorAuthenticated()) {
      router.replace('/doctor/login');
      return;
    }
    setReady(true);
  }, [router]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.getPublicHealthTrends());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load trends.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (ready) void load();
  }, [ready, load]);

  if (!ready) return null;

  return (
    <div className="min-h-screen bg-paper">
      <header className="flex items-center justify-between border-b border-rule px-6 py-3">
        <div className="flex items-baseline gap-4">
          <h1 className="text-[15px] font-semibold tracking-tight text-ink">Clinical Assistant</h1>
          <span className="label-meta">Public health trends</span>
        </div>
        <Link
          href="/doctor"
          className="text-[13px] text-ink-muted underline underline-offset-2 hover:text-ink"
        >
          Back to queue
        </Link>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-8">
        <SectionTitle note={data ? `Last ${data.window_days} days` : undefined}>
          Symptom trends
        </SectionTitle>

        <p className="mt-4 max-w-reading text-[13px] leading-relaxed text-ink-muted">
          Aggregated, anonymized case counts only — no patient names, case IDs, or
          transcript content. Symptoms with fewer than {data?.min_bucket_count ?? 3}{' '}
          total cases in the window are not shown, so no individual visit is ever
          identifiable from this page.
        </p>

        {error && <ErrorNotice message={error} onRetry={load} />}

        {loading && (
          <div className="mt-8">
            <Spinner label="Loading trends…" />
          </div>
        )}

        {!loading && data && data.trends.length === 0 && (
          <p className="mt-8 text-[13px] text-ink-faint">
            Not enough handed-off cases yet to show any trends.
          </p>
        )}

        {!loading && data && data.trends.length > 0 && (
          <ul className="mt-6 divide-y divide-rule border border-rule">
            {data.trends.map((trend) => (
              <TrendRow key={trend.symptom} trend={trend} />
            ))}
          </ul>
        )}
      </main>
    </div>
  );
}

function TrendRow({ trend }: { trend: SymptomTrend }) {
  const maxCount = Math.max(1, ...trend.daily_counts.map((d) => d.count));

  return (
    <li className="px-4 py-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="text-[14px] font-medium capitalize text-ink">{trend.symptom}</span>
          {trend.flagged && (
            <span className="inline-flex items-center gap-1.5 border border-risk-urgent/40 bg-risk-urgent-soft px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.1em] text-risk-urgent">
              ↑ {trend.ratio}x baseline
            </span>
          )}
        </div>
        <span className="label-meta">{trend.total_count} total</span>
      </div>

      <div className="mt-3 flex items-end gap-1" aria-hidden>
        {trend.daily_counts.map((point) => (
          <div
            key={point.date}
            title={`${point.date}: ${point.count}`}
            className={cx(
              'w-4 rounded-sm',
              point.count > 0 ? 'bg-ink-faint' : 'bg-rule',
            )}
            style={{ height: `${Math.max(4, (point.count / maxCount) * 28)}px` }}
          />
        ))}
      </div>

      <p className="mt-2 text-[12px] text-ink-muted">
        {trend.insufficient_history
          ? 'Not enough history yet to establish a baseline — showing raw counts only.'
          : `Today: ${trend.recent_count} · recent daily baseline: ${trend.baseline_avg}`}
      </p>
    </li>
  );
}
