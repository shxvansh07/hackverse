/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        paper: 'hsl(var(--paper))',
        surface: {
          DEFAULT: 'hsl(var(--surface))',
          sunken: 'hsl(var(--surface-sunken))',
        },
        ink: {
          DEFAULT: 'hsl(var(--ink))',
          muted: 'hsl(var(--ink-muted))',
          faint: 'hsl(var(--ink-faint))',
        },
        rule: {
          DEFAULT: 'hsl(var(--rule))',
          strong: 'hsl(var(--rule-strong))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          soft: 'hsl(var(--accent-soft))',
        },
        risk: {
          low: 'hsl(var(--risk-low))',
          'low-soft': 'hsl(var(--risk-low-soft))',
          uncertain: 'hsl(var(--risk-uncertain))',
          'uncertain-soft': 'hsl(var(--risk-uncertain-soft))',
          urgent: 'hsl(var(--risk-urgent))',
          'urgent-soft': 'hsl(var(--risk-urgent-soft))',
        },
        draft: {
          DEFAULT: 'hsl(var(--draft))',
          soft: 'hsl(var(--draft-soft))',
        },
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        // Editorial scale: display sizes get tight tracking, body stays loose
        // enough to read a dosing instruction without slipping a line.
        display: ['2.75rem', { lineHeight: '1.08', letterSpacing: '-0.028em' }],
        title: ['1.75rem', { lineHeight: '1.2', letterSpacing: '-0.02em' }],
        heading: ['1.125rem', { lineHeight: '1.35', letterSpacing: '-0.011em' }],
        body: ['0.9375rem', { lineHeight: '1.65' }],
        meta: ['0.6875rem', { lineHeight: '1.4', letterSpacing: '0.12em' }],
      },
      maxWidth: {
        reading: '62ch',
      },
      borderRadius: {
        DEFAULT: 'var(--radius)',
      },
    },
  },
  plugins: [],
};
