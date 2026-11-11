# Parser sidecars

External parsers run as separate containers so that untrusted documents never
execute inside the API process. Each sidecar implements one small contract:

```
GET  /health  -> {"status":"ok", ...}
POST /parse   multipart `file`
     -> {"blocks":[...], "markdown":"...", "metadata":{...}}
```

`blocks` may be omitted; Baryon AX then parses `markdown`. Block shapes:

```json
{"type":"heading","text":"예산 지침","level":1,"page":1}
{"type":"paragraph","text":"..."}
{"type":"table","page":2,"table":{"header_rows":1,"rows":[["구분","2026"],["인건비","110"]]}}
```

kordoc returns `{"table":{"cells":[[{"text","colSpan","rowSpan"}]],"hasHeader":true}, "pageNumber":1}`;
`baryon.ingest.sidecar` normalizes that shape (row spans are flattened).

## kordoc (HWP 3.x/5.x, HWPX, HWPML)

```bash
docker build -t baryon-kordoc:0.6 deploy/parsers/kordoc
docker run --rm -p 18080:8080 baryon-kordoc:0.6
curl -F "file=@sample.hwpx" http://127.0.0.1:18080/parse
```

* Node 20, `KORDOC_OFFLINE=1`, read-only root, no-new-privileges, 512 MB tmpfs.
* Upload cap is `MAX_UPLOAD_BYTES` (default 60 MB in the image; compose passes
  `MAX_DOCUMENT_BYTES`).
* Parse timeout is `KORDOC_PARSE_TIMEOUT_MS`.

## docling (PDF, Office, images)

```bash
docker build -t baryon-docling:0.6 deploy/parsers/docling
docker run --rm -p 18000:8000 -v docling-models:/models baryon-docling:0.6
curl -F "file=@sample.pdf" http://127.0.0.1:18000/parse
```

* Python 3.12 + `docling`, Markdown output (headings and GFM tables preserved).
* `DOCLING_ARTIFACTS_PATH=/models` makes docling use only local artifacts. When
  the volume is empty, prefetch once (network required):

  ```bash
  docker exec baryon-ax-docling-1 docling-tools models download \
    layout tableformer rapidocr \
    --rapidocr-backend-lang torch:ch --rapidocr-backend-lang torch:iso:ko \
    -o /models
  docker compose -f docker-compose.yml -f docker-compose.parsers.yml restart docling
  ```

  `torch:ch` / `torch:iso:ko` add Chinese and Korean OCR checkpoints. Without
  prefetch, the first PDF conversion fails with `RapidOCR artifacts not found`.
* The prefetched volume makes later conversions offline and removes first-request
  latency (≈10-30 s per document on CPU).
* Conversion is CPU-heavy: keep `OMP_NUM_THREADS` at or below the container CPU
  quota, and `mem_limit` at 6 GB. A GPU build is not included.

## Tika (legacy formats)

```bash
docker run --rm -p 19998:9998 apache/tika:latest
```

Tika is the fallback for formats no other adapter claims (`.doc`, `.xls`,
`.ppt`, RTF, OLE2 storage). Text quality is lower than docling; use it only as a
safety net.

## Operating notes

* Sidecars are optional and profile-gated (`--profile parsers`). Without them
  the API accepts text/markdown only.
* Parsers are stateless: scale them horizontally, but keep `mem_limit` in place
  because office/PDF parsing has spiky memory use.
* For confidential corpora, run sidecars with `--network none` and pass files
  over a mounted spool directory instead of HTTP, or keep them on an isolated
  compose network. kordoc is offline by default.
* Pin image digests before production. `kordoc` is installed from npm at build
  time; record the resolved version in the image label or lockfile.
