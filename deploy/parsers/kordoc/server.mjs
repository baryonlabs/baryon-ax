// Minimal HTTP wrapper around kordoc for Baryon AX ingest.
//
//   GET  /health -> {"status":"ok","parser":"kordoc","version":...}
//   POST /parse  multipart `file` -> {"blocks":[...],"markdown":"...","metadata":{...}}
//
// The contract matches baryon.ingest.sidecar.parse_response. Files are written
// to a private temp directory and removed after parsing. kordoc runs offline
// (KORDOC_OFFLINE=1) and inside the container's own namespace.

import http from "node:http";
import path from "node:path";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";

import Busboy from "busboy";
import { parse } from "kordoc";

const PORT = Number(process.env.PORT || 8080);
const MAX_BYTES = Number(process.env.MAX_UPLOAD_BYTES || 60 * 1024 * 1024);
const PARSE_TIMEOUT_MS = Number(process.env.KORDOC_PARSE_TIMEOUT_MS || 120000);
const VERSION = process.env.KORDOC_VERSION || "unknown";

function readUpload(req) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      fn(value);
    };

    let busboy;
    try {
      busboy = Busboy({ headers: req.headers, limits: { files: 1, fileSize: MAX_BYTES } });
    } catch (error) {
      finish(reject, new Error(`invalid multipart request: ${error.message}`));
      return;
    }

    let truncated = false;
    busboy.on("file", (_field, stream, info) => {
      const chunks = [];
      stream.on("data", (chunk) => chunks.push(chunk));
      stream.on("limit", () => {
        truncated = true;
      });
      stream.on("end", () => {
        if (truncated) {
          finish(reject, new Error("file exceeds MAX_UPLOAD_BYTES"));
          return;
        }
        finish(resolve, { filename: info.filename || "upload.bin", payload: Buffer.concat(chunks) });
      });
    });
    busboy.on("error", (error) => finish(reject, error));
    busboy.on("close", () => {
      if (!settled) finish(reject, new Error("no file part in request"));
    });

    setTimeout(() => finish(reject, new Error("upload timed out")), 30000).unref();
    req.pipe(busboy);
  });
}

async function parseBuffer(filename, payload) {
  const suffix = path.extname(filename) || ".bin";
  const workdir = await mkdtemp(path.join(tmpdir(), "kordoc-"));
  const target = path.join(workdir, `upload${suffix}`);
  try {
    await writeFile(target, payload);
    const result = await Promise.race([
      parse(target),
      new Promise((_, reject) => setTimeout(() => reject(new Error("parse timed out")), PARSE_TIMEOUT_MS).unref()),
    ]);
    if (!result || result.success === false) {
      throw new Error((result && result.error) || "kordoc reported failure");
    }
    return {
      blocks: Array.isArray(result.blocks) ? result.blocks : [],
      markdown: typeof result.markdown === "string" ? result.markdown : "",
      metadata: result.metadata && typeof result.metadata === "object" ? result.metadata : {},
    };
  } finally {
    await rm(workdir, { recursive: true, force: true });
  }
}

function send(res, status, body) {
  const payload = Buffer.from(JSON.stringify(body), "utf8");
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", "content-length": payload.length });
  res.end(payload);
}

const server = http.createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/health") {
    send(res, 200, { status: "ok", parser: "kordoc", version: VERSION, offline: process.env.KORDOC_OFFLINE === "1" });
    return;
  }
  if (req.method !== "POST" || req.url !== "/parse") {
    send(res, 404, { error: "not found" });
    return;
  }
  try {
    const { filename, payload } = await readUpload(req);
    const result = await parseBuffer(filename, payload);
    send(res, 200, result);
  } catch (error) {
    send(res, 422, { error: String(error && error.message ? error.message : error) });
  }
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`kordoc sidecar listening on :${PORT}`);
});

for (const signal of ["SIGTERM", "SIGINT"]) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
