import { defineConfig } from '@playwright/test'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../', import.meta.url))
const api = 'http://127.0.0.1:8017'
const frontend = 'http://127.0.0.1:5178'
const python = process.platform === 'win32' ? './.venv/Scripts/python.exe' : './.venv/bin/python'

export default defineConfig({
  testDir: './e2e',
  timeout: 45_000,
  use: {
    baseURL: frontend,
    channel: process.env.PLAYWRIGHT_CHANNEL,
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: `"${python}" -m uvicorn tests.e2e_app:app --host 127.0.0.1 --port 8017`,
      cwd: root,
      url: `${api}/openapi.json`,
    },
    {
      command: 'npm run dev -- --port 5178',
      env: { EXOCORTEX_API_PROXY_TARGET: api },
      url: frontend,
    },
  ],
})
