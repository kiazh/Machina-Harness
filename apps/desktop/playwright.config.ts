import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "test/e2e",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:1420", channel: "msedge", headless: true, viewport: { width: 1440, height: 900 } },
  webServer: { command: "npm run dev", url: "http://127.0.0.1:1420", reuseExistingServer: true },
  reporter: "list",
});
