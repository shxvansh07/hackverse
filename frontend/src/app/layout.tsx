import type { Metadata, Viewport } from 'next';
import './globals.css';

/**
 * System font stacks rather than next/font/google.
 *
 * A webfont fetch at build time makes the build fail without network — not a
 * risk worth carrying at a hackathon, and it costs a render-blocking request
 * on the patient's phone. The stacks below resolve to the platform UI face
 * everywhere, and critically include Indic-capable fallbacks so Devanagari,
 * Tamil, Telugu and the rest render correctly rather than as tofu.
 */

export const metadata: Metadata = {
  title: 'Multilingual AI Clinical Assistant',
  description:
    'Multilingual patient intake with deterministic safety triage and doctor-authorised prescribing.',
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: '#fcfcfc',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
