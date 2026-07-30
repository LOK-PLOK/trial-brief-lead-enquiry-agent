import type { Config } from 'tailwindcss'

// Tailwind v4 auto-detects source files via the `@tailwindcss/vite` plugin
// (see vite.config.ts), so `content` globs are not required here. This file
// exists as the explicit place to extend the theme once the UI is built —
// per the brief, the UI should stay "functional rather than designed", so
// keep this minimal rather than building out a custom design system.
export default {
  theme: {
    extend: {
      // TODO(client/tailwind.config): add custom colors/spacing here only if
      // actually needed; prefer Tailwind's defaults.
    },
  },
} satisfies Config
