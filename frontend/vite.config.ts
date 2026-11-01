import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// In development the browser talks to Vite, which forwards /api to the FastAPI backend.
const backend = process.env.VITE_DEV_BACKEND ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': backend } },
  preview: { proxy: { '/api': backend } },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    restoreMocks: true,
  },
})
