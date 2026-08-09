'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { ApiError, api } from '@/lib/api';
import { ErrorNotice } from '@/components/ui/clinical';

type Mode = 'login' | 'register';

export default function DoctorLoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>('login');

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [qualification, setQualification] = useState('');
  const [registrationNo, setRegistrationNo] = useState('');
  const [yearsExperience, setYearsExperience] = useState('');
  const [specialty, setSpecialty] = useState('');

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
      if (mode === 'login') {
        await api.doctorLogin(username, password);
      } else {
        await api.doctorRegister({
          username,
          password,
          name,
          qualification,
          registrationNo,
          yearsExperience: yearsExperience ? Number(yearsExperience) : undefined,
          specialty,
        });
      }
      router.replace('/doctor');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col bg-paper">
      <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-6 py-16">
        <p className="label-meta">Clinical Assistant</p>
        <h1 className="mt-3 text-title font-semibold text-ink">
          {mode === 'login' ? 'Doctor sign in' : 'Create a doctor account'}
        </h1>
        <p className="mt-3 text-body leading-relaxed text-ink-muted">
          {mode === 'login'
            ? 'Review incoming patient cases and decide on prescriptions.'
            : 'Every account has its own login — case notes and decisions are attributed to whichever doctor is signed in.'}
        </p>

        <form onSubmit={submit} className="mt-10 space-y-4">
          {mode === 'register' && (
            <label className="block">
              <span className="label-meta">Full name</span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Dr. Priya Nair, MD"
                required
                className="field mt-1.5"
              />
            </label>
          )}

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
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              required
              minLength={mode === 'register' ? 8 : undefined}
              className="field mt-1.5"
            />
          </label>

          {mode === 'register' && (
            <>
              <label className="block">
                <span className="label-meta">Qualification (optional)</span>
                <input
                  value={qualification}
                  onChange={(e) => setQualification(e.target.value)}
                  placeholder="e.g. MBBS, MD (General Medicine)"
                  className="field mt-1.5"
                />
              </label>
              <label className="block">
                <span className="label-meta">Registration no. (optional)</span>
                <input
                  value={registrationNo}
                  onChange={(e) => setRegistrationNo(e.target.value)}
                  placeholder="e.g. KMC/00000/2026"
                  className="field mt-1.5"
                />
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="label-meta">Years experience (optional)</span>
                  <input
                    type="number"
                    min={0}
                    value={yearsExperience}
                    onChange={(e) => setYearsExperience(e.target.value)}
                    placeholder="e.g. 6"
                    className="field mt-1.5"
                  />
                </label>
                <label className="block">
                  <span className="label-meta">Specialty (optional)</span>
                  <input
                    value={specialty}
                    onChange={(e) => setSpecialty(e.target.value)}
                    placeholder="e.g. Pediatrics"
                    className="field mt-1.5"
                  />
                </label>
              </div>
            </>
          )}

          {error && <ErrorNotice message={error} />}

          <button type="submit" disabled={busy} className="btn-primary w-full">
            {busy
              ? mode === 'login' ? 'Signing in…' : 'Creating account…'
              : mode === 'login' ? 'Sign in' : 'Create account'}
          </button>
        </form>

        <button
          type="button"
          onClick={() => {
            setMode(mode === 'login' ? 'register' : 'login');
            setError(null);
          }}
          className="mt-4 text-[13px] text-ink-muted underline underline-offset-2 hover:text-ink"
        >
          {mode === 'login' ? 'Create an account instead' : 'Sign in instead'}
        </button>

        {mode === 'login' && (
          <p className="mt-8 border-t border-rule pt-5 text-[12px] leading-relaxed text-ink-faint">
            Demo credentials are set via the DOCTOR_USERNAME and DOCTOR_PASSWORD environment
            variables on the backend. This is a hackathon prototype and not production
            authentication.
          </p>
        )}
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
