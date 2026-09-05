/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      // Semantic tokens -> CSS variables (see src/index.css) so dark mode swaps
      // in one place instead of a `dark:` variant on every element.
      colors: {
        bg: 'rgb(var(--c-bg) / <alpha-value>)',
        surface: 'rgb(var(--c-surface) / <alpha-value>)',
        elevated: 'rgb(var(--c-elevated) / <alpha-value>)',
        line: 'rgb(var(--c-border) / <alpha-value>)',
        'line-strong': 'rgb(var(--c-border-strong) / <alpha-value>)',
        ink: 'rgb(var(--c-text) / <alpha-value>)',
        'ink-2': 'rgb(var(--c-text-2) / <alpha-value>)',
        muted: 'rgb(var(--c-muted) / <alpha-value>)',
        primary: 'rgb(var(--c-primary) / <alpha-value>)',
        'primary-dark': 'rgb(var(--c-primary-dark) / <alpha-value>)',
        ai: 'rgb(var(--c-ai) / <alpha-value>)',
        'ai-bg': 'rgb(var(--c-ai-bg) / <alpha-value>)',
        'ai-line': 'rgb(var(--c-ai-border) / <alpha-value>)',
        'ai-head': 'rgb(var(--c-ai-heading) / <alpha-value>)',
        success: 'rgb(var(--c-success) / <alpha-value>)',
        warning: 'rgb(var(--c-warning) / <alpha-value>)',
        failure: 'rgb(var(--c-failure) / <alpha-value>)',
        info: 'rgb(var(--c-info) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['Inter var', 'Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      boxShadow: {
        card: '0 1px 2px 0 rgb(15 23 42 / 0.04)',
        pop: '0 8px 24px -6px rgb(15 23 42 / 0.16), 0 2px 6px -2px rgb(15 23 42 / 0.08)',
      },
    },
  },
  plugins: [],
}
