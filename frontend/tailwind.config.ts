import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        background: 'hsl(var(--background))', surface: 'hsl(var(--surface))', elevated: 'hsl(var(--elevated))',
        border: 'hsl(var(--border))', foreground: 'hsl(var(--foreground))', muted: 'hsl(var(--muted))',
        primary: 'hsl(var(--primary))', positive: 'hsl(var(--positive))', negative: 'hsl(var(--negative))', warning: 'hsl(var(--warning))',
      },
      boxShadow: { panel: 'var(--shadow-panel)', glow: 'var(--shadow-glow)' },
      borderRadius: { panel: 'var(--radius-panel)' },
      backgroundImage: { hero: 'var(--gradient-hero)', sheen: 'var(--gradient-sheen)', page: 'var(--gradient-page)' },
      fontFamily: { sans: ['Inter', 'PingFang SC', 'system-ui', 'sans-serif'], mono: ['JetBrains Mono', 'SFMono-Regular', 'monospace'] },
      animation: { pulseSoft: 'pulseSoft 2.8s ease-in-out infinite', rise: 'rise .45s ease-out both' },
      keyframes: { pulseSoft: {'0%,100%':{opacity:'.55'},'50%':{opacity:'1'}}, rise: {from:{opacity:'0',transform:'translateY(8px)'},to:{opacity:'1',transform:'translateY(0)'}} }
    },
  },
  plugins: [],
} satisfies Config
