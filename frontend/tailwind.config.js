/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: { 50: '#f6f7f9', 100: '#eceef2', 200: '#d9dde5', 300: '#b8bfcc', 400: '#8a93a6', 500: '#657089', 600: '#4b556b', 700: '#363f53', 800: '#232a3a', 900: '#151a27', 950: '#0d111b' },
        brand: { 50: '#eef4ff', 100: '#dbe7ff', 500: '#2f6bdc', 600: '#2457c5', 700: '#1d46a0' },
        risk: { critical: '#b42318', high: '#c2570c', medium: '#a16207', low: '#3f7d58', minimal: '#657089' },
      },
      fontFamily: { sans: ['Inter', 'ui-sans-serif', 'system-ui', 'Segoe UI', 'Roboto', 'sans-serif'], mono: ['ui-monospace', 'SFMono-Regular', 'Consolas', 'monospace'] },
      boxShadow: { panel: '0 1px 2px rgba(16,24,40,0.06)' },
    },
  },
  plugins: [],
}
