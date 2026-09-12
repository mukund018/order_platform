import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * In the container nginx serves the build and proxies /api/*, so the browser only ever
 * talks to one origin and no service needs CORS configured. `npm run dev` has to
 * reproduce that mapping exactly, or the dev build would need code the real build does
 * not - hence the same prefixes here, pointed at the published host ports.
 */
const proxy = (target: string, rewriteTo = "") => ({
  target,
  changeOrigin: true,
  rewrite: (path: string) => path.replace(/^\/api\/[^/]+/, rewriteTo),
});

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api/orders": proxy("http://localhost:8001"),
      "/api/inventory": proxy("http://localhost:8002"),
      "/api/payments": proxy("http://localhost:8003"),
      "/api/support": proxy("http://localhost:8004", "/support"),
      "/api/prom": proxy("http://localhost:9090"),
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
