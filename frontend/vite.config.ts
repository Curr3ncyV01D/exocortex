import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    include: ['tests/**/*.test.{ts,tsx}'],
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    coverage: {
      include: ['src/**/*.{ts,tsx}'],
      reporter: ['text-summary', 'html'],
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api/': process.env.EXOCORTEX_API_PROXY_TARGET ?? 'http://127.0.0.1:8000',
    },
  },
})
