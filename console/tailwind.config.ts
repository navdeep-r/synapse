import type { Config } from 'tailwindcss'
import defaultTheme from 'tailwindcss/defaultTheme'
import typography from '@tailwindcss/typography'

export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        paper: 'var(--color-paper)',
        ink: {
          DEFAULT: 'var(--color-ink)',
          muted: 'var(--color-ink-muted)',
        },
        ledger: 'var(--color-ledger)',
        signal: 'var(--color-signal)',
        alert: 'var(--color-alert)',
        confirmed: 'var(--color-confirmed)',
        hairline: 'var(--color-hairline)',
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', ...defaultTheme.fontFamily.sans],
        serif: ['"IBM Plex Serif"', ...defaultTheme.fontFamily.serif],
        mono: ['"IBM Plex Mono"', ...defaultTheme.fontFamily.mono],
      },
      fontSize: {
        'display': ['2.25rem', { lineHeight: '1.2', fontWeight: '600' }],
        'h1': ['1.5rem', { lineHeight: '1.2', fontWeight: '600' }],
        'h2': ['1.125rem', { lineHeight: '1.2', fontWeight: '600' }],
        'body': ['0.9375rem', { lineHeight: '1.5', fontWeight: '400' }],
        'narrative': ['1.0625rem', { lineHeight: '1.6', fontWeight: '400' }],
        'evidence': ['0.8125rem', { lineHeight: '1.5', fontWeight: '500' }],
        'caption': ['0.75rem', { lineHeight: '1.5', fontWeight: '400' }],
      },
      screens: {
        'xs': '480px',
        'sm': '768px',
        'md': '1024px',
        'lg': '1280px',
      },
      maxWidth: {
        'content': '1280px',
      }
    },
  },
  plugins: [
    typography,
    require('tailwindcss-animate'),
  ],
} satisfies Config
