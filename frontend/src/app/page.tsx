'use client';

import React from 'react';
import Link from 'next/link';

export default function Home() {
  return (
    <div className="min-h-screen bg-white text-black flex flex-col justify-between p-6 sm:p-12">
      {/* Header */}
      <header className="max-w-4xl mx-auto w-full border-b border-neutral-200 pb-4">
        <h1 className="text-base font-bold tracking-tight text-black">Clinical Assistant Portal</h1>
        <p className="text-xs text-neutral-600">Multilingual Patient Triage & Physician Handoff</p>
      </header>

      {/* Main Container */}
      <main className="my-auto max-w-3xl mx-auto w-full py-12 space-y-8">
        <div className="space-y-2">
          <h2 className="text-2xl sm:text-4xl font-extrabold tracking-tight text-black">
            Clinical Intake & Review
          </h2>
          <p className="text-sm text-neutral-600">
            Select a portal to continue.
          </p>
        </div>

        {/* Portal Options */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Link
            href="/patient"
            className="border border-neutral-300 hover:border-black p-6 rounded-lg bg-neutral-50 transition-colors space-y-3"
          >
            <div className="text-xs uppercase font-mono tracking-wider text-neutral-500">Portal 01</div>
            <h3 className="text-lg font-bold text-black">Patient Intake Assistant</h3>
            <p className="text-xs text-neutral-600 leading-relaxed">
              Describe symptoms via text or voice. Receive intake assessment and physician-approved prescription.
            </p>
            <div className="text-xs font-bold text-black pt-2 underline underline-offset-4">
              Enter Patient Portal →
            </div>
          </Link>

          <Link
            href="/doctor/login"
            className="border border-neutral-300 hover:border-black p-6 rounded-lg bg-neutral-50 transition-colors space-y-3"
          >
            <div className="text-xs uppercase font-mono tracking-wider text-neutral-500">Portal 02</div>
            <h3 className="text-lg font-bold text-black">Physician Dashboard</h3>
            <p className="text-xs text-neutral-600 leading-relaxed">
              Authenticated access for licensed physicians to review intake summaries, edit drafts, and approve cases.
            </p>
            <div className="text-xs font-bold text-black pt-2 underline underline-offset-4">
              Doctor Sign In →
            </div>
          </Link>
        </div>
      </main>

      {/* Footer */}
      <footer className="max-w-4xl mx-auto w-full border-t border-neutral-200 pt-4 text-xs text-neutral-500 flex justify-between">
        <span>Clinical Intake System</span>
        <span>Physician Final Authority</span>
      </footer>
    </div>
  );
}
