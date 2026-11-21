import { Check, Copy, ExternalLink, Route } from "lucide-react";
import { useState } from "react";

export type GatewayProvider = {
  name: string; up: boolean; ok: number; fail: number; last_error: string;
  last_ok_at: string | null; key_required: boolean; key_set: boolean;
};
export type GatewayInfo = {
  endpoint: string | null; model: string; api_key_configured: boolean;
  status_configured: boolean; dashboard_url: string | null;
  status: null | {
    reachable: boolean; error: string;
    endpoints: { label: string; url: string }[];
    models: Record<string, { provider: string; model: string }[]>;
    providers: GatewayProvider[];
    stats: { requests: number; fallbacks: number; failed: number; served: Record<string, number> };
  };
};

const TEXT = {
  ko: {
    title: "LLM 게이트웨이 접속 정보", endpoint: "접속 주소", model: "모델 이름", apiKey: "API 키",
    keySet: "설정됨", keyUnset: "설정 안 됨", keyNote: "값은 표시하지 않습니다.",
    order: "폴백 순서와 실시간 상태", backends: "백엔드별 정보", realModel: "실제 모델", served: "처리",
    failures: "실패", lastError: "마지막 오류", none: "없음", healthy: "정상", down: "장애", noKey: "API 키 없음",
    totals: (r: number, f: number, x: number) => `요청 ${r}건 · 폴백 ${f}건 · 실패 ${x}건`,
    examples: "사용 예시", copy: "복사", copied: "복사됨", dashboard: "게이트웨이 대시보드",
    unreachable: "게이트웨이 상태를 가져오지 못했습니다", noStatus: "상태 주소(MODEL_GATEWAY_STATUS_URL)가 설정되지 않아 폴백 상태를 표시하지 않습니다.",
    notConnected: "모델 서버가 연결되지 않았습니다(demo-local).",
  },
  en: {
    title: "LLM gateway connection", endpoint: "Endpoint", model: "Model name", apiKey: "API key",
    keySet: "Configured", keyUnset: "Not configured", keyNote: "The value is never shown.",
    order: "Fallback order and live status", backends: "Backends", realModel: "Model", served: "Served",
    failures: "Failures", lastError: "Last error", none: "None", healthy: "Healthy", down: "Down", noKey: "No API key",
    totals: (r: number, f: number, x: number) => `${r} requests · ${f} fallbacks · ${x} failed`,
    examples: "Usage examples", copy: "Copy", copied: "Copied", dashboard: "Gateway dashboard",
    unreachable: "Could not read the gateway status", noStatus: "No status URL (MODEL_GATEWAY_STATUS_URL) is configured, so fallback status is not shown.",
    notConnected: "No model server is connected (demo-local).",
  },
};

function CopyButton({ value, label, done }: { value: string; label: string; done: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button type="button" className="ghost small" aria-label={`${label}: ${value.split("\n")[0]}`}
      onClick={async () => {
        try { await navigator.clipboard.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard blocked */ }
      }}>
      {copied ? <Check size={13}/> : <Copy size={13}/>}{copied ? done : label}
    </button>
  );
}

export function providerState(provider: GatewayProvider): "healthy" | "down" | "noKey" {
  if (provider.key_required && !provider.key_set) return "noKey";
  return provider.up ? "healthy" : "down";
}

export default function GatewayCard({ gateway, locale }: { gateway: GatewayInfo; locale: "ko" | "en" }) {
  const t = TEXT[locale];
  const status = gateway.status;
  const providers = status?.providers ?? [];
  const chain = status ? Object.values(status.models)[0] ?? [] : [];
  const realModel = (name: string) => chain.find(step => step.provider === name)?.model ?? "—";
  const endpoints = status?.endpoints.length ? status.endpoints : gateway.endpoint ? [{ label: "api", url: gateway.endpoint }] : [];
  const base = endpoints[0]?.url ?? "http://127.0.0.1:8030/v1";
  const curl = `curl ${base}/chat/completions \\\n  -H "Authorization: Bearer $MODEL_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "${gateway.model}", "messages": [{"role": "user", "content": "hello"}]}'`;
  const sdk = `from openai import OpenAI\nclient = OpenAI(base_url="${base}", api_key=os.environ["MODEL_API_KEY"])\nclient.chat.completions.create(model="${gateway.model}", messages=[{"role": "user", "content": "hello"}])`;

  return (
    <section className="setting-card system-card gateway-card" aria-labelledby="gateway-card-title">
      <h3 id="gateway-card-title"><Route size={15}/>{t.title}</h3>
      {!gateway.endpoint && <p className="muted">{t.notConnected}</p>}
      <dl>
        {endpoints.map(item => <div key={item.url}>
          <dt>{t.endpoint} · {item.label}</dt>
          <dd className="gateway-copy"><code>{item.url}</code><CopyButton value={item.url} label={t.copy} done={t.copied}/></dd>
        </div>)}
        <div><dt>{t.model}</dt><dd className="gateway-copy"><code>{gateway.model}</code><CopyButton value={gateway.model} label={t.copy} done={t.copied}/></dd></div>
        <div><dt>{t.apiKey}</dt><dd>{gateway.api_key_configured ? t.keySet : t.keyUnset} <small className="muted">{t.keyNote}</small></dd></div>
      </dl>

      {!gateway.status_configured && <p className="muted">{t.noStatus}</p>}
      {status && !status.reachable && <p className="gateway-warning" role="status">{t.unreachable}{status.error ? `: ${status.error}` : ""}</p>}

      {providers.length > 0 && <>
        <h4>{t.order}</h4>
        <ol className="gateway-chain">
          {providers.map((provider, index) => {
            const state = providerState(provider);
            return <li key={provider.name} className={`gateway-step ${state}`}>
              <span className="gateway-index">{index + 1}</span>
              <strong>{provider.name}</strong>
              <span className={`gateway-badge ${state}`}>{t[state]}</span>
            </li>;
          })}
        </ol>
        {status && <p className="muted">{t.totals(status.stats.requests, status.stats.fallbacks, status.stats.failed)}</p>}

        <h4>{t.backends}</h4>
        <div className="table-wrap">
          <table className="gateway-table">
            <thead><tr><th>{t.backends}</th><th>{t.realModel}</th><th>{t.served}</th><th>{t.failures}</th><th>{t.lastError}</th></tr></thead>
            <tbody>{providers.map(provider => <tr key={provider.name}>
              <td>{provider.name}</td>
              <td><code>{realModel(provider.name)}</code></td>
              <td>{status?.stats.served[provider.name] ?? provider.ok}</td>
              <td>{provider.fail}</td>
              <td>{provider.last_error || t.none}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </>}

      <details className="gateway-examples">
        <summary>{t.examples}</summary>
        <div className="gateway-example"><pre><code>{curl}</code></pre><CopyButton value={curl} label={t.copy} done={t.copied}/></div>
        <div className="gateway-example"><pre><code>{sdk}</code></pre><CopyButton value={sdk} label={t.copy} done={t.copied}/></div>
      </details>

      {gateway.dashboard_url && <div className="link-row">
        <a href={gateway.dashboard_url} target="_blank" rel="noreferrer"><ExternalLink size={13}/>{t.dashboard}</a>
      </div>}
    </section>
  );
}
