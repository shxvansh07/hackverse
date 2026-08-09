'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { ApiError, api } from '@/lib/api';
import { ErrorNotice } from '@/components/ui/clinical';

export default function DoctorLoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (api.isDoctorAuthenticated()) router.replace('/doctor');
  }, [router]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.doctorLogin(username, password);
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
        <div className="card-raised px-7 py-9 sm:px-9">
          <span className="eyebrow">Clinical Assistant</span>
          <h1 className="mt-3 text-[1.75rem] font-semibold leading-tight tracking-[-0.022em] text-ink">
            Doctor sign in
          </h1>
          <p className="mt-3 text-[15px] leading-relaxed text-ink-muted">
            Review incoming patient cases and decide on prescriptions.
          </p>

          <form onSubmit={submit} className="mt-8 space-y-5">
            <label className="block">
              <span className="eyebrow">Username</span>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
                className="field mt-2"
              />
            </label>

            <label className="block">
              <span className="eyebrow">Password</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
                className="field mt-2"
              />
            </label>

            {error && <ErrorNotice message={error} />}

            <button type="submit" disabled={busy} className="btn-primary w-full">
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        </div>

        <p className="mt-6 px-2 text-[12px] leading-relaxed text-ink-faint">
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
