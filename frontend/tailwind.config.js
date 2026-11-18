/** @type {import('tailwindcss').Config} */
import animate from "tailwindcss-animate";

export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: { center: true, padding: "1.5rem", screens: { "2xl": "1440px" } },
    extend: {
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
      },
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        popover: { DEFAULT: "hsl(var(--popover))", foreground: "hsl(var(--popover-foreground))" },
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        surface: "hsl(var(--surface))",
        sidebar: {
          DEFAULT: "hsl(var(--sidebar))", foreground: "hsl(var(--sidebar-foreground))",
          muted: "hsl(var(--sidebar-muted))", accent: "hsl(var(--sidebar-accent))", border: "hsl(var(--sidebar-border))",
        },
        good: { DEFAULT: "hsl(var(--good))", soft: "hsl(var(--good-soft))" },
        warn: { DEFAULT: "hsl(var(--warn))", soft: "hsl(var(--warn-soft))" },
        bad: { DEFAULT: "hsl(var(--bad))", soft: "hsl(var(--bad-soft))" },
        idle: { DEFAULT: "hsl(var(--idle))", soft: "hsl(var(--idle-soft))" },
      },
      borderRadius: { lg: "0.75rem", md: "0.6rem", sm: "0.4rem" },
      keyframes: {
        "pulse-ring": { "0%": { boxShadow: "0 0 0 0 hsl(var(--good) / .45)" }, "100%": { boxShadow: "0 0 0 6px hsl(var(--good) / 0)" } },
      },
      animation: { "pulse-ring": "pulse-ring 1.8s ease-out infinite" },
    },
  },
  plugins: [animate],
};
