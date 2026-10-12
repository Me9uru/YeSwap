// 只用于视觉验收，所有 API 在内存中隔离，绝不代理到用户后端。
import { createServer } from "vite";
import { fixtures } from "./fixtures.js";
const server = await createServer({
  server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy: {} },
  plugins: [
    {
      name: "isolated-preview",
      configureServer(server) {
        server.middlewares.use("/api", (req, res) => {
          const path = new URL(req.url, "http://localhost").pathname;
          res.setHeader("Content-Type", "application/json");
          if (req.method !== "GET") {
            res.statusCode = 403;
            res.end(
              JSON.stringify({ ok: false, error: "隔离验收环境不执行写操作" }),
            );
            return;
          }
          if (!(path in fixtures)) {
            res.statusCode = 404;
            res.end(JSON.stringify({ ok: false, error: "没有此验收样例" }));
            return;
          }
          res.end(JSON.stringify(fixtures[path]));
        });
      },
    },
  ],
});
await server.listen();
server.printUrls();
