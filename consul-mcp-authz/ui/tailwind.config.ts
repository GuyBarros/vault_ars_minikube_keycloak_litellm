import type { Config } from 'tailwindcss';

// Consul UI design tokens — derived from the screenshots provided.
// Sidebar uses a near-black with high-contrast text; the content
// surface is white with subtle neutral borders. Inter is the
// HashiCorp UI font.
const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['var(--font-inter)', 'Inter', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      colors: {
        // Sidebar surface
        nav: {
          bg: '#1B1C1F',
          surface: '#27292E',
          border: '#2F3137',
          text: '#E5E7EB',
          muted: '#8A8F98',
          selected: '#33363D',
        },
        // Content surface
        content: {
          bg: '#FFFFFF',
          subtle: '#F7F8F9',
          border: '#E5E7EB',
          divider: '#EDEEF0',
          ink: '#0B0C0E',
          muted: '#6B6F76',
        },
        // Accent — HashiCorp Electric Blue (used by Consul's primary CTA)
        brand: {
          DEFAULT: '#1B49E2',
          hover: '#173BB7',
          ring: '#A9BCFB',
          tint: '#EFF3FE',
        },
        // Allow / success
        allow: {
          bg: '#E6F4EA',
          border: '#B7DFC1',
          ink: '#1A7240',
        },
        // Status badges
        status: {
          bg: '#F1F2F5',
          ink: '#3A3D45',
          ring: '#D6D8DE',
        },
        consul: {
          // Consul logo pink/coral
          accent: '#CA2171',
        },
      },
      boxShadow: {
        focus: '0 0 0 2px #FFFFFF, 0 0 0 4px #1B49E2',
      },
      borderRadius: {
        chip: '3px',
      },
    },
  },
  plugins: [],
};

export default config;
