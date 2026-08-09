'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { ApiError, api, type DoctorDirectoryEntry } from '@/lib/api';
import { ErrorNotice } from '@/components/ui/clinical';

export default function DoctorLoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [doctors, setDoctors] = useState<DoctorDirectoryEntry[]>([]);
  const [doctorId, setDoctorId] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (api.isDoctorAuthenticated()) router.replace('/doctor');
  }, [router]);

  useEffect(() => {
    api.listDoctors().then((list) => {
      setDoctors(list);
      if (list.length > 0) setDoctorId(list[0].doctor_id);
    }).catch(() => {
      // Optional — the account still works with the default identity.
    });
  }, []);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.doctorLogin(username, password, doctorId || undefined);
      router.replace('/doctor');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Sign in failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col bg-paper">
      <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-6 py-16">
        <p className="label-meta">Clinical Assistant</p>
        <h1 className="mt-3 text-title font-semibold text-ink">Doctor sign in</h1>
        <p className="mt-3 text-body leading-relaxed text-ink-muted">
          Review incoming patient cases and decide on prescriptions.
        </p>

        <form onSubmit={submit} className="mt-10 space-y-4">
          <label className="block">
            <span className="label-meta">Username</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
              className="field mt-1.5"
            />
          </label>

          <label className="block">
            <span className="label-meta">Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
              className="field mt-1.5"
            />
          </label>

          {doctors.length > 0 && (
            <label className="block">
              <span className="label-meta">Signing in as</span>
              <select
                value={doctorId}
                onChange={(e) => setDoctorId(e.target.value)}
                className="field mt-1.5"
              >
                {doctors.map((d) => (
                  <option key={d.doctor_id} value={d.doctor_id}>
                    {d.name} · {d.years_experience} yrs · {d.specialty}
                  </option>
                ))}
              </select>
            </label>
          )}

          {error && <ErrorNotice message={error} />}

          <button type="submit" disabled={busy} className="btn-primary w-full">
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <p className="mt-8 border-t border-rule pt-5 text-[12px] leading-relaxed text-ink-faint">
          Demo credentials are set via the DOCTOR_USERNAME and DOCTOR_PASSWORD environment
          variables on the backend. This is a hackathon prototype and not production
          authentication.
        </p>
      </div>

      <footer className="border-t border-rule">
        <div className="mx-auto flex max-w-md items-center justify-between px-6 py-4">
          <span className="label-meta">Doctor</span>
          <Link href="/patient" className="text-[13px] text-ink-muted underline underline-offset-2">
            Patient intake
          </Link>
        </div>
      </footer>
    </div>
  );
}
