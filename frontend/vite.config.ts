import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

declare const process: { env: Record<string, string | undefined> };

export default defineConfig(({ command }) => {
  const backendPort = process.env.MAESTRO_PORT?.trim() || "8765";
  if (
    command === "serve" &&
    (!/^\d+$/.test(backendPort) ||
      Number(backendPort) < 1 ||
      Number(backendPort) > 65535)
  ) {
    throw new Error(
      "Set MAESTRO_PORT to the running backend's port (1–65535). Start the backend first when using -Port 0; its launcher sets MAESTRO_PORT to the assigned port.",
    );
  }
  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      proxy: { "/api": `http://127.0.0.1:${backendPort}` },
      fs: { strict: true },
    },
  };
});
