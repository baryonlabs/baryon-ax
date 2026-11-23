import { FormEvent, useCallback, useEffect, useState } from "react";
import { Globe, Square } from "lucide-react";
import { apiFetch } from "./auth";

type Crawl = {
  id: string;
  url: string;
  max_pages: number;
  path_prefix: string;
  state: "queued" | "running" | "done" | "failed" | "cancelled";
  pages_done: number;
  pages: { id: string; title: string; url: string }[];
  error: string;
};

const text = {
  ko: {
    title: "웹사이트 크롤링",
    body: "사이트 주소를 넣으면 같은 사이트의 페이지를 사이트맵과 링크로 찾아 읽고, 문서로 저장합니다. robots.txt를 지키고 사내망 주소는 읽지 않습니다.",
    url: "https://www.example.com/",
    prefix: "경로 제한 (선택, 예: /docs)",
    pages: "최대 쪽 수",
    start: "크롤링 시작",
    stop: "멈추기",
    states: { queued: "대기", running: "읽는 중", done: "완료", failed: "실패", cancelled: "멈춤" },
    progress: (done: number, max: number) => `${done} / ${max}쪽`,
  },
  en: {
    title: "Crawl a website",
    body: "Give a site address: pages on the same site are found through its sitemap and links, read and stored as documents. robots.txt is respected and internal addresses are never read.",
    url: "https://www.example.com/",
    prefix: "Path limit (optional, e.g. /docs)",
    pages: "Max pages",
    start: "Start crawl",
    stop: "Stop",
    states: { queued: "Queued", running: "Reading", done: "Done", failed: "Failed", cancelled: "Stopped" },
    progress: (done: number, max: number) => `${done} / ${max} pages`,
  },
};

/** Start a whole-site crawl (POST /v1/crawls) and watch it until it finishes. */
export default function WebCrawl({ api, headers, locale, category, tags, onPages }: {
  api: string;
  headers: Record<string, string>;
  locale: "ko" | "en";
  category: string;
  tags: string;
  onPages: () => void;
}) {
  const t = text[locale];
  const [url, setUrl] = useState("");
  const [prefix, setPrefix] = useState("");
  const [maxPages, setMaxPages] = useState(30);
  const [crawls, setCrawls] = useState<Crawl[]>([]);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const headerKey = JSON.stringify(headers);

  const load = useCallback(async () => {
    const response = await apiFetch(`${api}/v1/crawls`, { headers });
    if (response.ok) setCrawls((await response.json()).data);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, headerKey]);

  useEffect(() => { void load(); }, [load]);

  const active = crawls.some(crawl => crawl.state === "queued" || crawl.state === "running");
  const progressKey = crawls.map(crawl => `${crawl.id}:${crawl.pages_done}:${crawl.state}`).join(",");
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
  }, [active, load]);
  // New pages arrived: refresh the document list under this card.
  useEffect(() => { if (crawls.length) onPages(); }, [progressKey]); // eslint-disable-line react-hooks/exhaustive-deps

  async function start(event: FormEvent) {
    event.preventDefault();
    setProblem("");
    setBusy(true);
    try {
      const response = await apiFetch(`${api}/v1/crawls`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim(), max_pages: maxPages, path_prefix: prefix.trim(), category: category || "web", tags }),
      });
      if (!response.ok) {
        const detail = (await response.json().catch(() => ({}))).detail;
        setProblem(typeof detail === "string" ? detail : `${response.status}`);
        return;
      }
      setUrl("");
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function stop(id: string) {
    await apiFetch(`${api}/v1/crawls/${id}`, { method: "DELETE", headers });
    await load();
  }

  return <section className="web-crawl" aria-label={t.title}>
    <div className="upload-zone-head">
      <Globe size={22}/>
      <div><strong>{t.title}</strong><p>{t.body}</p></div>
    </div>
    <form className="web-crawl-form" onSubmit={start}>
      <input type="url" required value={url} onChange={event => setUrl(event.target.value)} placeholder={t.url} aria-label={t.title}/>
      <input value={prefix} onChange={event => setPrefix(event.target.value)} placeholder={t.prefix} aria-label={t.prefix}/>
      <label className="web-crawl-pages">{t.pages}
        <select value={maxPages} onChange={event => setMaxPages(Number(event.target.value))}>
          {[10, 30, 100, 200].map(count => <option key={count} value={count}>{count}</option>)}
        </select>
      </label>
      <button className="primary" disabled={busy || !url.trim()}>{t.start}</button>
    </form>
    {problem && <p className="upload-note error">{problem}</p>}
    {crawls.length > 0 && <ul className="web-crawl-jobs">
      {crawls.slice(0, 5).map(crawl => {
        const running = crawl.state === "queued" || crawl.state === "running";
        return <li key={crawl.id} className={`web-crawl-job ${crawl.state}`}>
          <div className="web-crawl-job-head">
            <span className="web-crawl-url">{crawl.url}{crawl.path_prefix && crawl.path_prefix !== "/" ? ` · ${crawl.path_prefix}` : ""}</span>
            <span className="web-crawl-state">{t.states[crawl.state]} · {t.progress(crawl.pages_done, crawl.max_pages)}</span>
            {running && <button className="ghost small" onClick={() => void stop(crawl.id)}><Square size={12}/>{t.stop}</button>}
          </div>
          <progress max={crawl.max_pages} value={crawl.pages_done}/>
          {crawl.error && <small className="upload-note error">{crawl.error}</small>}
        </li>;
      })}
    </ul>}
  </section>;
}
