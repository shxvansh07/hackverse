import Link from 'next/link';

/**
 * Project landing page.
 *
 * Every number on this page is read off the running system rather than
 * invented for the pitch — the knowledge counts come from /api/health and the
 * language list from /api/languages. They are inlined rather than fetched so
 * the page still renders if the backend is down, but they are checked facts,
 * and STATS is the one place to update when the knowledge base grows.
 */

const STATS = [
  { value: '8', label: 'Indian languages' },
  { value: '20', label: 'Red-flag rules' },
  { value: '34', label: 'Clinical protocols' },
  { value: '42', label: 'ICD-10 codes' },
];

const LANGUAGES = [
  { native: 'English', english: 'English' },
  { native: 'हिन्दी', english: 'Hindi' },
  { native: 'বাংলা', english: 'Bengali' },
  { native: 'मराठी', english: 'Marathi' },
  { native: 'தமிழ்', english: 'Tamil' },
  { native: 'తెలుగు', english: 'Telugu' },
  { native: 'ગુજરાતી', english: 'Gujarati' },
  { native: 'ಕನ್ನಡ', english: 'Kannada' },
];

const STEPS = [
  {
    icon: 'translate.svg',
    step: '01',
    title: 'Describe it in your own language',
    body: 'The patient speaks or types in any of eight languages. A structured history is collected one question at a time — symptoms, duration, allergies, existing conditions, current medication.',
  },
  {
    icon: 'engine.svg',
    step: '02',
    title: 'A deterministic layer classifies the case',
    body: 'Red flags are matched by application logic against a curated reference, never decided by a model. Anything dangerous or incomplete is routed to a clinician instead of a draft.',
  },
  {
    icon: 'edit.svg',
    step: '03',
    title: 'A doctor makes every decision',
    body: 'The doctor sees an English summary, the safety signal, and — only where it is permitted — a draft to approve, modify or reject. Nothing reaches the patient unapproved.',
  },
];

const SAFEGUARDS = [
  {
    icon: 'lock.svg',
    title: 'Urgent cases never get a draft',
    body: 'A matched red flag short-circuits the prescription pathway entirely and escalates to a doctor, with emergency guidance shown to the patient immediately.',
  },
  {
    icon: 'engine.svg',
    title: 'The model never decides risk',
    body: 'An LLM can raise concern but can never lower it. Routing reads the deterministic assessment only — a model that goes down, or goes wrong, cannot make a case look safer.',
  },
  {
    icon: 'transcript.svg',
    title: 'Drafts are grounded, not improvised',
    body: 'Medications come from a curated formulary by retrieval. The model writes rationale in plain language; it never chooses a drug or a dose.',
  },
  {
    icon: 'clock.svg',
    title: 'Nothing is assumed',
    body: 'A question that was never asked is never recorded as answered. Missing clinical facts keep a case uncertain rather than letting it pass as routine.',
  },
];

/**
 * Decorative only — every icon sits beside a heading that already carries the
 * meaning, so announcing it again would just be noise.
 *
 * Most of these assets are self-contained tiles: a white rounded card and its
 * own soft border are baked into the SVG alongside the glyph. They are drawn
 * for roughly 44px and must not be nested inside another background chip —
 * the baked-in card then covers the chip and the thin blue glyph washes out.
 */
function Icon({ src, className = '' }: { src: string; className?: string }) {
  return <img src={`/landing/${src}`} alt="" aria-hidden className={className} />;
}

export default function Home() {
  return (
    <div className="min-h-screen bg-paper">
      <header className="sticky top-0 z-20 border-b border-rule bg-paper/90 backdrop-blur">
        <nav className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-4">
          <span className="whitespace-nowrap text-[15px] font-semibold tracking-tight text-ink">
            Clinical Assistant
          </span>
          <div className="flex items-center gap-2 sm:gap-3">
            {/* Three items do not fit on a 375px bar without both the brand
                and this link wrapping to two lines. The doctor route is still
                one tap away from the hero and the closing CTA. */}
            <Link
              href="/doctor"
              className="hidden whitespace-nowrap rounded-full px-3 py-2 text-[13px] text-ink-muted transition-colors hover:text-ink sm:inline-flex"
            >
              Doctor sign in
            </Link>
            <Link
              href="/patient"
              className="whitespace-nowrap rounded-full bg-ink px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-ink/90"
            >
              Start an intake
            </Link>
          </div>
        </nav>
      </header>

      <main>
        {/* ---------------------------------------------------------- hero */}
        <section className="mx-auto max-w-6xl px-6 pb-20 pt-16 sm:pt-24">
          <div className="grid items-center gap-14 lg:grid-cols-[1.05fr_1fr]">
            <div className="reveal">
              <span className="inline-flex items-center gap-2 rounded-full border border-rule bg-surface px-3 py-1.5 text-[12px] text-ink-muted">
                {/* globe is a solid-fill glyph, so unlike the tile icons it
                    still reads at badge size. */}
                <Icon src="globe.svg" className="h-4 w-4" />
                Eight languages, one clinical record
              </span>

              <h1 className="mt-6 max-w-[16ch] text-[2.5rem] font-semibold leading-[1.06] tracking-[-0.03em] text-ink sm:text-[3.25rem]">
                Care that starts in your own language.
              </h1>

              <p className="mt-6 max-w-reading text-[17px] leading-relaxed text-ink-muted">
                Patients describe symptoms the way they actually speak. The assistant
                collects a structured history, a deterministic safety layer classifies the
                case, and a doctor makes every clinical decision.{' '}
                <span className="text-ink">The AI drafts; the doctor decides.</span>
              </p>

              <div className="mt-9 flex flex-wrap items-center gap-3">
                <Link
                  href="/patient"
                  className="rounded-full bg-ink px-6 py-3 text-[15px] font-medium text-white transition-colors hover:bg-ink/90"
                >
                  Start an intake
                </Link>
                <Link
                  href="/doctor"
                  className="rounded-full border border-rule-strong bg-surface px-6 py-3 text-[15px] font-medium text-ink transition-colors hover:bg-surface-sunken"
                >
                  Doctor portal
                </Link>
              </div>

              <p className="mt-5 text-[13px] text-ink-faint">
                Runs with no AI provider configured — the safety layer is unaffected.
              </p>
            </div>

            {/* Product preview. Built in markup rather than a screenshot so it
                stays sharp, responsive and honest about the real interface. */}
            <div className="reveal">
              <div className="rounded-2xl border border-rule bg-surface p-3 shadow-[0_1px_2px_rgba(16,24,40,.04),0_12px_32px_-12px_rgba(16,24,40,.12)]">
                <div className="rounded-xl bg-surface-sunken p-5">
                  <div className="flex items-center justify-between">
                    <span className="label-meta">Patient intake</span>
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-risk-low/30 bg-risk-low-soft px-2 py-1 text-[10px] font-medium uppercase tracking-[0.1em] text-risk-low">
                      Low risk
                    </span>
                  </div>

                  <div className="mt-5 space-y-3">
                    <Bubble side="ai">आपको क्या तकलीफ़ है?</Bubble>
                    <Bubble side="patient">मुझे 3 दिन से बुख़ार है</Bubble>
                    <Bubble side="ai">Any allergies to medicines?</Bubble>
                  </div>

                  <div className="mt-5 rounded-lg border border-rule bg-surface p-3">
                    <span className="label-meta">Safety layer</span>
                    <ul className="mt-2 space-y-1.5 text-[12px] leading-relaxed text-ink-muted">
                      <li className="flex gap-2">
                        <Icon src="check.svg" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                        Symptoms and duration captured
                      </li>
                      <li className="flex gap-2">
                        <Icon src="check.svg" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                        No red flag matched
                      </li>
                      <li className="flex gap-2 text-ink-faint">
                        <span className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded-full border border-rule-strong" />
                        Allergies not yet established
                      </li>
                    </ul>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* --------------------------------------------------------- stats */}
        <section className="border-y border-rule bg-surface">
          <dl className="mx-auto grid max-w-6xl grid-cols-2 gap-px bg-rule sm:grid-cols-4">
            {STATS.map((s) => (
              <div key={s.label} className="reveal bg-surface px-6 py-8 text-center">
                <dt className="text-[2rem] font-semibold tracking-tight text-ink">{s.value}</dt>
                <dd className="mt-1 text-[13px] text-ink-muted">{s.label}</dd>
              </div>
            ))}
          </dl>
        </section>

        {/* ------------------------------------------------------ how it works */}
        <section className="mx-auto max-w-6xl px-6 py-24">
          <div className="reveal max-w-reading">
            <span className="label-meta">How it works</span>
            <h2 className="mt-3 text-[2rem] font-semibold leading-tight tracking-[-0.022em] text-ink">
              Three steps, and a human at the end of every one.
            </h2>
          </div>

          <ol className="mt-14 grid gap-6 lg:grid-cols-3">
            {STEPS.map((s) => (
              <li
                key={s.step}
                className="reveal rounded-2xl border border-rule bg-surface p-7 transition-colors hover:bg-surface-sunken"
              >
                <div className="flex items-center justify-between">
                  <Icon src={s.icon} className="h-11 w-11" />
                  <span className="text-[12px] font-medium tracking-[0.12em] text-ink-faint">
                    {s.step}
                  </span>
                </div>
                <h3 className="mt-5 text-heading font-semibold text-ink">{s.title}</h3>
                <p className="mt-2.5 text-[14px] leading-relaxed text-ink-muted">{s.body}</p>
              </li>
            ))}
          </ol>
        </section>

        {/* ------------------------------------------------------- safeguards */}
        <section className="border-t border-rule bg-surface-sunken">
          <div className="mx-auto max-w-6xl px-6 py-24">
            <div className="reveal max-w-reading">
              <span className="label-meta">Why it is safe to run</span>
              <h2 className="mt-3 text-[2rem] font-semibold leading-tight tracking-[-0.022em] text-ink">
                The safety layer does not depend on the model behaving.
              </h2>
              <p className="mt-4 text-[15px] leading-relaxed text-ink-muted">
                Every routing decision is made by ordinary application logic against a
                curated clinical reference. The language model handles language — it is
                never the thing standing between a patient and a prescription.
              </p>
            </div>

            <div className="mt-14 grid gap-6 sm:grid-cols-2">
              {SAFEGUARDS.map((s) => (
                <div
                  key={s.title}
                  className="reveal rounded-2xl border border-rule bg-surface p-7"
                >
                  <Icon src={s.icon} className="h-11 w-11" />
                  <h3 className="mt-5 text-heading font-semibold text-ink">{s.title}</h3>
                  <p className="mt-2.5 text-[14px] leading-relaxed text-ink-muted">{s.body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* -------------------------------------------------------- languages */}
        <section className="mx-auto max-w-6xl px-6 py-24">
          <div className="reveal flex flex-wrap items-end justify-between gap-6">
            <div className="max-w-reading">
              <span className="label-meta">Languages</span>
              <h2 className="mt-3 text-[2rem] font-semibold leading-tight tracking-[-0.022em] text-ink">
                A patient should not have to translate their own symptoms.
              </h2>
            </div>
            <Icon src="translate.svg" className="h-14 w-14" />
          </div>

          <ul className="mt-12 grid grid-cols-2 gap-px border border-rule bg-rule sm:grid-cols-4">
            {LANGUAGES.map((l) => (
              <li key={l.english} className="reveal bg-surface px-5 py-6">
                <span lang={l.english === 'English' ? 'en' : undefined} className="block text-[1.35rem] text-ink">
                  {l.native}
                </span>
                <span className="mt-1 block text-[13px] text-ink-faint">{l.english}</span>
              </li>
            ))}
          </ul>
        </section>

        {/* -------------------------------------------------------------- cta */}
        <section className="mx-auto max-w-6xl px-6 pb-24">
          <div className="reveal rounded-2xl border border-rule bg-surface px-8 py-14 text-center sm:px-14">
            <Icon src="assistant.svg" className="mx-auto h-12 w-12" />
            <h2 className="mx-auto mt-6 max-w-[22ch] text-[2rem] font-semibold leading-tight tracking-[-0.022em] text-ink">
              See an intake run end to end.
            </h2>
            <p className="mx-auto mt-4 max-w-reading text-[15px] leading-relaxed text-ink-muted">
              Start a session in any language, or sign in to the doctor portal to review the
              queue, the safety signal and the draft.
            </p>
            <div className="mt-9 flex flex-wrap justify-center gap-3">
              <Link
                href="/patient"
                className="rounded-full bg-ink px-6 py-3 text-[15px] font-medium text-white transition-colors hover:bg-ink/90"
              >
                Start an intake
              </Link>
              <Link
                href="/doctor"
                className="rounded-full border border-rule-strong bg-surface px-6 py-3 text-[15px] font-medium text-ink transition-colors hover:bg-surface-sunken"
              >
                Doctor portal
              </Link>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-rule">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-8">
          <p className="max-w-reading text-[12px] leading-relaxed text-ink-faint">
            Prototype for demonstration. Not a medical device and not for clinical use. In an
            emergency, contact your local emergency services.
          </p>
          <span className="label-meta">Multilingual intake</span>
        </div>
      </footer>
    </div>
  );
}

function Bubble({ side, children }: { side: 'ai' | 'patient'; children: React.ReactNode }) {
  const fromAi = side === 'ai';
  return (
    <div className={fromAi ? 'flex justify-start' : 'flex justify-end'}>
      <p
        className={
          fromAi
            ? 'max-w-[85%] rounded-2xl rounded-tl-sm border border-rule bg-surface px-3.5 py-2.5 text-[13px] leading-relaxed text-ink'
            : 'max-w-[85%] rounded-2xl rounded-tr-sm bg-accent px-3.5 py-2.5 text-[13px] leading-relaxed text-white'
        }
      >
        {children}
      </p>
    </div>
  );
}
