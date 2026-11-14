/** @type {import('tailwindcss').Config} */
// Colours point at CSS variables in src/styles/tokens.css. Use these names, never raw hex.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: v('bg'),
        surface: v('surface'),
        panel: v('panel'),
        line: v('line'),
        ink: { DEFAULT: v('ink'), 2: v('ink-2'), 3: v('ink-3') },
        ok: { DEFAULT: v('ok'), bg: v('ok-bg') },
        warn: { DEFAULT: v('warn'), bg: v('warn-bg') },
        bad: { DEFAULT: v('bad'), bg: v('bad-bg') },
        info: { DEFAULT: v('info'), bg: v('info-bg') },
        sensor: { DEFAULT: v('sensor'), bg: v('sensor-bg') },
        plum: { DEFAULT: v('plum'), bg: v('plum-bg') },
        brand: { DEFAULT: v('brand'), bg: v('brand-bg') },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        display: ['"Plus Jakarta Sans"', 'Inter', 'system-ui', 'sans-serif'],
      },
      borderRadius: { card: '18px' },
      boxShadow: {
        card: '0 1px 2px rgba(22,32,42,0.04), 0 4px 16px rgba(22,32,42,0.05)',
        lift: '0 2px 4px rgba(22,32,42,0.05), 0 12px 32px rgba(22,32,42,0.09)',
      },
    },
  },
  plugins: [],
};
