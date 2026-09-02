import { defineConfig } from "@playwright/test";

const pythonCommand = process.env.SMOKE_PYTHON
  || (process.env.CI ? "python" : process.platform === "win32" ? "..\\.venv\\Scripts\\python.exe" : "python");

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60000,
  expect: {
    timeout: 10000,
  },
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "on-first-retry",
  },
  webServer: [
    {
      command: `${pythonCommand} -m uvicorn app.main:app --host 127.0.0.1 --port 8000`,
      cwd: "../backend",
      env: {
        ...process.env,
        PYTHONPATH: ".",
        FINANCE_DB_PATH: "../data/e2e-finance.sqlite3",
      },
      reuseExistingServer: false,
      timeout: 60000,
      url: "http://127.0.0.1:8000/health",
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 5173",
      reuseExistingServer: false,
      timeout: 60000,
      url: "http://127.0.0.1:5173",
    },
  ],
});
