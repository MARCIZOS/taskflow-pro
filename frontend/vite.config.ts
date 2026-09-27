import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// In development the API runs on :8000 and Vite proxies /api to it, so the
// browser only ever talks to one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
  test: {
    environment: "node",
  },
});
