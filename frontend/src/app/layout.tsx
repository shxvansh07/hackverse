import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Multilingual AI Clinical Assistant',
  description: 'Patient-first multilingual triage, AI safety assessment, and doctor-approved prescriptions',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-navy-950 text-slate-100 antialiased selection:bg-clinical-500 selection:text-white">
        {children}
      </body>
    </html>
  );
}
