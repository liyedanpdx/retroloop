import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Same-origin in development, exactly as nginx makes it same-origin in the
    // built image (`frontend/nginx.conf` proxies /api/ to the backend). Every
    // request in `src/api` is therefore a relative path, and the refresh cookie
    // is a first-party cookie in both.
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./src/test-setup.ts",
  },
});
