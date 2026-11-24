// Временный статик-сервер для локальной проверки GitHub Pages-раскладки
// (сайт в подпапке /karta-okruzheniya). Имитирует поведение Pages:
// подпапка → корень ветки gh-pages.
// Использование: node scripts/serve-test.mjs <корень-где-лежит-подпапка> [порт]
import { createServer } from "node:http";
import { open, readFile, stat } from "node:fs/promises";
import { join, normalize, extname } from "node:path";

const root = process.argv[2] ?? ".";
const port = Number(process.argv[3] ?? 3000);

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".geojson": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".txt": "text/plain; charset=utf-8",
};

// GitHub Pages на неизвестный путь отдаёт 404.html, ближайший вверх по дереву
// (на проде — из корня опубликованной ветки, т.е. <root>/<basePath>/404.html).
// Без этого старые ссылки /r/<id>/ в e2e проверять нечем.
async function readNotFoundPage(urlPath) {
  let dir = urlPath.endsWith("/") ? urlPath : urlPath.replace(/\/[^/]*$/, "/");
  for (;;) {
    const candidate = normalize(join(root, dir, "404.html"));
    if (candidate.startsWith(normalize(root))) {
      try {
        return await readFile(candidate);
      } catch {
        // 404.html на этом уровне нет — поднимаемся выше
      }
    }
    if (dir === "" || dir === "/") return null;
    dir = dir.replace(/\/[^/]+\/$/, "/");
  }
}

createServer(async (req, res) => {
  const urlPath = decodeURIComponent(new URL(req.url, "http://x").pathname);
  try {
    let path = urlPath;
    if (path.endsWith("/")) path += "index.html";
    const file = normalize(join(root, path));
    if (!file.startsWith(normalize(root))) {
      res.writeHead(403).end("forbidden");
      return;
    }
    const fileStat = await stat(file);
    const headers = {
      "content-type": MIME[extname(file)] ?? "application/octet-stream",
      "content-length": fileStat.size,
      "accept-ranges": "bytes",
      "cache-control": "no-cache",
    };
    const rangeHeader = req.headers.range;
    if (rangeHeader) {
      const match = /^bytes=(\d*)-(\d*)$/.exec(rangeHeader);
      if (!match || fileStat.size === 0) {
        res.writeHead(416, { "content-range": `bytes */${fileStat.size}` }).end();
        return;
      }
      let start = match[1] ? Number(match[1]) : 0;
      let end = match[2] ? Number(match[2]) : fileStat.size - 1;
      if (!match[1] && match[2]) {
        const suffixLength = Number(match[2]);
        start = Math.max(0, fileStat.size - suffixLength);
        end = fileStat.size - 1;
      }
      if (
        !Number.isSafeInteger(start) ||
        !Number.isSafeInteger(end) ||
        start >= fileStat.size ||
        end < start
      ) {
        res.writeHead(416, { "content-range": `bytes */${fileStat.size}` }).end();
        return;
      }
      end = Math.min(end, fileStat.size - 1);
      const body = Buffer.alloc(end - start + 1);
      const handle = await open(file, "r");
      try {
        await handle.read(body, 0, body.length, start);
      } finally {
        await handle.close();
      }
      res.writeHead(206, {
        ...headers,
        "content-length": body.length,
        "content-range": `bytes ${start}-${end}/${fileStat.size}`,
      });
      res.end(body);
      return;
    }
    res.writeHead(200, headers);
    if (req.method === "HEAD") {
      res.end();
      return;
    }
    res.end(await readFile(file));
  } catch {
    const page = await readNotFoundPage(urlPath);
    if (page) {
      res.writeHead(404, {
        "content-type": "text/html; charset=utf-8",
        "content-length": page.length,
        "cache-control": "no-cache",
      });
      res.end(page);
      return;
    }
    res.writeHead(404).end("not found");
  }
}).listen(port, () => console.log(`serving ${root} on http://localhost:${port}`));
