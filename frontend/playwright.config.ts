import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  workers: 1,
  fullyParallel: false,
  use: {
    baseURL: "http://127.0.0.1:8777",
    browserName: "chromium",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
  },
  webServer: {
    command: `${process.platform === "win32" ? "..\\.venv\\Scripts\\python.exe" : "../.venv/bin/python"} ../tests/serve_preview.py`,
    url: "http://127.0.0.1:8777/health",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
