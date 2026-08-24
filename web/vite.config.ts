import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

/**
 * Two things here are not defaults, and both exist for a reason the design
 * states.
 *
 * **The dev proxy.** The SPA talks to `/web/*` with cookies, and the session
 * cookie is `SameSite=Lax` + `Secure` (WEB-1). Served from a different origin the
 * browser would not send it at all, so the Vite server proxies the API instead of
 * the app calling an absolute URL — same origin, cookies work, and no CORS
 * configuration to get wrong in production later.
 *
 * Two settings on the backend side make local development work, and skipping them
 * costs a confusing hour: `RELAY_SESSION_COOKIE_SECURE=false` (a browser silently
 * drops a Secure cookie over http) and `RELAY_WEB_ORIGINS` including this dev
 * origin (the CSRF check refuses an unrecognised `Origin` on writes).
 */
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      "/web": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
      // The filesystem carrier serves attachment bytes from the app itself; with
      // MinIO the signed URL is absolute and never comes through here (S-25).
      "/blobs": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
