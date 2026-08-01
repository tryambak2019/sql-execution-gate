import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";

export default defineConfig({
  plugins: [react()],
  base: "/app/",
  resolve: {
    alias: {
      "@": path.resolve(new URL(".", import.meta.url).pathname, "./src"),
    },
  },
  server: {
    host: true,
    allowedHosts: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        secure: false,
        rewrite: (requestPath) => requestPath.replace(/^\/api/, ""),
      },
    },
  },
});
