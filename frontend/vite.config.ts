import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";
import { loadEnv, type UserConfig } from "vite";

const sourceRoot = fileURLToPath(new URL("./src", import.meta.url));

interface FrontendViteOptions {
  input?: Record<string, string>;
  outDir?: string;
}

export function createFrontendViteConfig(
  mode: string,
  options: FrontendViteOptions = {},
): UserConfig {
  const env = loadEnv(mode, process.cwd(), "");
  // 기본값은 공유 라이브 백엔드 — 클론 후 env 없이 바로 붙는다.
  // 로컬 백엔드로 개발하려면 VITE_BACKEND_ORIGIN=http://127.0.0.1:8000 으로 덮어쓴다.
  const backendOrigin = env.VITE_BACKEND_ORIGIN || "https://k8s.woonyong.org";
  const backendUrl = new URL(backendOrigin);
  const localBackend = backendUrl.hostname === "127.0.0.1" || backendUrl.hostname === "localhost";
  const backendApiPrefix = normalizeApiPrefix(
    env.VITE_BACKEND_API_PREFIX ?? (localBackend ? "" : "/api"),
  );
  const proxy = {
    "/api": {
      target: backendUrl.origin,
      changeOrigin: true,
      cookieDomainRewrite: "",
      ws: true,
      rewrite: (path: string) => `${backendApiPrefix}${path.replace(/^\/api/u, "")}` || "/",
    },
  };

  return {
    plugins: [react(), tailwindcss()],
    optimizeDeps: {
      // 단일 React 인스턴스로 사전번들해 dev에서 @dnd-kit 등이 별도 React 사본을
      // 물어 "Invalid hook call"이 나는 것을 막는다.
      include: [
        "react",
        "react-dom",
        "react/jsx-runtime",
        "tailwind-merge",
        "@tabler/icons-react",
        "lucide-react",
        "motion/react",
        "@dnd-kit/core",
        "@dnd-kit/sortable",
        "@dnd-kit/modifiers",
        "@dnd-kit/utilities",
      ],
    },
    resolve: { alias: { "@": sourceRoot }, dedupe: ["react", "react-dom"] },
    server: { proxy },
    preview: { proxy },
    test: {
      hookTimeout: 15_000,
      testTimeout: 15_000,
      maxWorkers: 4,
      setupFiles: ["./src/test/setup.ts"],
    },
    build: {
      chunkSizeWarningLimit: 900,
      outDir: options.outDir,
      rollupOptions: {
        input: options.input,
        output: {
          onlyExplicitManualChunks: true,
          manualChunks(id) {
            if (id.includes("@xyflow/react") || id.includes("elkjs")) return "flow";
            if (id.includes("cmdk")) return "overlays";
            if (/node_modules\/(?:framer-motion|motion|motion-dom|motion-utils)\//u.test(id)) return "motion";
            return undefined;
          },
        },
      },
    },
  };
}

function normalizeApiPrefix(value: string): string {
  const trimmed = value.trim();
  if (!trimmed || trimmed === "/") return "";
  return `/${trimmed.replace(/^\/+|\/+$/gu, "")}`;
}

export default defineConfig(({ mode }) => createFrontendViteConfig(mode));
