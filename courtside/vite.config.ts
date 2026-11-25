import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // `npm run dev` serves the pages only; the API runs under
  // `wrangler pages dev`, which this proxies to.
  server: { proxy: { "/api": "http://127.0.0.1:8788" } },
});
