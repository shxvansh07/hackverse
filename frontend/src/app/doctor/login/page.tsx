'use client';

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import Link from 'next/link';

export default function DoctorLoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState('doctor');
  const [password, setPassword] = useState('doctorpassword123');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      await api.doctorLogin(username, password);
      router.push('/doctor');
    } catch (err: any) {
      setError(err.message || 'Invalid credentials.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-white text-black flex items-center justify-center p-4">
      <div className="max-w-md w-full bg-neutral-50 border border-neutral-300 rounded-lg p-6 space-y-6">
        <div className="flex justify-between items-center border-b border-neutral-200 pb-3">
          <Link href="/" className="text-xs text-neutral-600 hover:text-black font-semibold">
            ← Back to Home
          </Link>
          <span className="text-xs font-mono text-neutral-600">Physician Auth</span>
        </div>

        <div className="space-y-1">
          <h1 className="text-xl font-bold text-black">Doctor Sign In</h1>
          <p className="text-xs text-neutral-600">Enter credentials to access clinical dashboard.</p>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-400 text-red-700 p-3 rounded text-xs font-semibold">
            {error}
          </div>
        )}

        <form onSubmit={handleLogin} className="space-y-4">
          <div className="space-y-1">
            <label className="block text-xs font-bold text-neutral-800">Username</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              className="w-full bg-white border border-neutral-300 rounded px-3 py-2 text-xs text-black focus:outline-none focus:border-black"
            />
          </div>

          <div className="space-y-1">
            <label className="block text-xs font-bold text-neutral-800">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="w-full bg-white border border-neutral-300 rounded px-3 py-2 text-xs text-black focus:outline-none focus:border-black"
            />
          </div>

          <div className="bg-white p-3 rounded border border-neutral-200 text-[11px] text-neutral-600 space-y-0.5 font-mono">
            <div>Username: doctor</div>
            <div>Password: doctorpassword123</div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 bg-black text-white font-bold rounded text-xs hover:bg-neutral-800 transition-colors disabled:opacity-50"
          >
            {loading ? 'Authenticating...' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  );
}
