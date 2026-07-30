import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // TODO(client/vite.config): during local dev (`npm run dev`), proxy
    // /api to the FastAPI server (e.g. http://localhost:8000) so the client
    // can call relative `/api/...` paths in both dev and the single-container
    // production build without an env-specific base URL.
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
