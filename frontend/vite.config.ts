import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In production the app is served from /vme/ (IIS application path).
// In dev we still serve from /, with Vite proxying /api/* to the backend.
const BASE_PATH = "/vme/";

export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === "build" ? BASE_PATH : "/",
  server: {
    port: 5173,
    proxy: {
      // Dev backend runs on :8001 (production service holds :8000).
      "/api": {
        target: "http://localhost:8001",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
}));
