import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Proxy API calls to the FastAPI backend so the browser treats them as
    // same-origin — the httpOnly session cookie just works, no CORS dance.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true, // proxy the /api/chat/ws WebSocket upgrade too
      },
    },
  },
})
