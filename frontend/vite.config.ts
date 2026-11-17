import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build straight into the FastAPI static folder; the API serves the SPA at /.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../backend/app/static", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000", "/health": "http://127.0.0.1:8000" },
  },
});
