import Link from 'next/link';

export default function Home() {
  return (
    <div className="flex min-h-screen flex-col bg-paper">
      <header className="border-b border-rule">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-4">
          <span className="text-[15px] font-semibold tracking-tight text-ink">
            Clinical Assistant
          </span>
          <span className="label-meta">Multilingual intake</span>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col justify-center px-6 py-20">
        <h1 className="max-w-[18ch] text-display font-semibold text-ink">
          A multilingual bridge between patient and doctor.
        </h1>

        <p className="mt-6 max-w-reading text-body leading-relaxed text-ink-muted">
          Patients describe their symptoms in their own language. The assistant collects a
          structured history, a deterministic safety layer classifies the case, and a doctor
          makes every clinical decision. The AI drafts; the doctor decides.
        </p>

        <div className="mt-14 grid gap-px border border-rule bg-rule sm:grid-cols-2">
          <Link
            href="/patient"
            className="group bg-surface px-6 py-8 transition-colors hover:bg-accent-soft"
          >
            <span className="label-meta">For patients</span>
            <h2 className="mt-3 text-heading font-semibold text-ink">Start an intake</h2>
            <p className="mt-2 max-w-reading text-[14px] leading-relaxed text-ink-muted">
              Speak or type in English, Hindi and six more Indian languages.
            </p>
            <span className="mt-4 inline-block text-[13px] text-accent underline underline-offset-4">
              Begin
            </span>
          </Link>

          <Link
            href="/doctor"
            className="group bg-surface px-6 py-8 transition-colors hover:bg-accent-soft"
          >
            <span className="label-meta">For doctors</span>
            <h2 className="mt-3 text-heading font-semibold text-ink">Review cases</h2>
            <p className="mt-2 max-w-reading text-[14px] leading-relaxed text-ink-muted">
              A live queue with English summaries, safety signals and AI drafts to approve,
              modify or reject.
            </p>
            <span className="mt-4 inline-block text-[13px] text-accent underline underline-offset-4">
              Sign in
            </span>
          </Link>
        </div>

        <dl className="mt-16 grid gap-8 border-t border-rule pt-8 sm:grid-cols-3">
          {[
            {
              term: 'Deterministic triage',
              detail:
                'Red flags are matched by application logic against a curated reference, not decided by a model.',
            },
            {
              term: 'Doctor authority',
              detail:
                'No AI draft reaches a patient. Only an approved or modified prescription is final.',
            },
            {
              term: 'Grounded drafting',
              detail:
                'Medications are retrieved from a curated formulary. The model writes rationale, never dosing.',
            },
          ].map((item) => (
            <div key={item.term}>
              <dt className="label-meta">{item.term}</dt>
              <dd className="mt-2 text-[14px] leading-relaxed text-ink-muted">{item.detail}</dd>
            </div>
          ))}
        </dl>
      </main>

      <footer className="border-t border-rule">
        <div className="mx-auto max-w-4xl px-6 py-5">
          <p className="max-w-reading text-[12px] leading-relaxed text-ink-faint">
            Prototype for demonstration. Not a medical device and not for clinical use. In an
            emergency, contact your local emergency services.
          </p>
        </div>
      </footer>
    </div>
  );
}
