import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  workers: 1,
  reporter: 'list',
  use: { baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:3000', browserName: 'chromium', viewport: { width: 1440, height: 900 }, timezoneId: 'Asia/Kolkata', screenshot: 'only-on-failure' }
});
