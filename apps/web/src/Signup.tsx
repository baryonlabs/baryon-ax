import { FormEvent, useEffect, useState } from "react";
import { ArrowLeft, Globe2, MailCheck, ShieldCheck } from "lucide-react";

type Locale = "ko" | "en";

const COPY = {
  ko: {
    eyebrow: "초대 코드 가입",
    title: "조직 만들기",
    body: "초대 코드를 받으셨나요? 코드와 이메일, 조직 이름을 넣으면 조직이 만들어지고 첫 관리자 초대 메일이 갑니다.",
    code: "초대 코드",
    codeHint: "예: AX-AB12-CD34-EF56",
    email: "관리자 이메일",
    emailHint: "초대를 받을 주소입니다.",
    orgName: "조직 이름",
    orgNameHint: "예: 아크미 주식회사",
    submit: "조직 만들기",
    submitting: "조직을 만드는 중…",
    required: "모든 항목을 입력해 주세요.",
    invalid: "초대 코드가 유효하지 않거나 만료되었습니다. 코드와 이메일을 확인해 주세요.",
    rateLimited: "시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.",
    provisionFailed: "조직을 만드는 중 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.",
    failed: "가입을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    successTitle: "메일을 확인하세요",
    successBody: (email: string) => `${email} 주소로 로그인 링크를 보냈습니다. 메일의 링크를 열면 첫 관리자로 시작할 수 있습니다.`,
    successNoMail: "메일 서버가 설정되지 않은 개발 환경입니다. 아래 링크로 바로 로그인하세요.",
    orgId: "조직 ID",
    openLogin: "로그인 화면으로",
    backHome: "홈으로",
    already: "이미 만들어진 조직입니다. 로그인 링크를 다시 보냈습니다.",
    language: "언어",
  },
  en: {
    eyebrow: "Invite-code signup",
    title: "Create your organization",
    body: "Have an invite code? Enter the code, your e-mail and the organization name. We will create the organization and invite its first administrator.",
    code: "Invite code",
    codeHint: "e.g. AX-AB12-CD34-EF56",
    email: "Administrator e-mail",
    emailHint: "The address that receives the invitation.",
    orgName: "Organization name",
    orgNameHint: "e.g. Acme Corporation",
    submit: "Create organization",
    submitting: "Creating the organization…",
    required: "Please fill in every field.",
    invalid: "The invitation code is invalid or has expired. Check the code and e-mail address.",
    rateLimited: "Too many attempts. Please try again later.",
    provisionFailed: "Something went wrong while creating the organization. Please try again shortly.",
    failed: "Signup could not be completed. Please try again shortly.",
    successTitle: "Check your e-mail",
    successBody: (email: string) => `We sent a sign-in link to ${email}. Open it to start as the first administrator.`,
    successNoMail: "This development environment has no mail server. Use the link below to sign in.",
    orgId: "Organization ID",
    openLogin: "Go to sign-in",
    backHome: "Back to home",
    already: "This organization already exists. We sent the sign-in link again.",
    language: "Language",
  },
} as const;

function initialLocale(): Locale {
  const saved = localStorage.getItem("baryon-locale");
  if (saved === "ko" || saved === "en") return saved;
  return navigator.language.toLowerCase().startsWith("ko") ? "ko" : "en";
}

type SignupResult = {
  organization_id: string;
  organization_name: string;
  email: string;
  email_sent: boolean;
  login_url?: string;
  already_redeemed?: boolean;
};

function messageFor(status: number, code: string, t: (typeof COPY)[Locale]): string {
  if (status === 429 || code === "rate_limited") return t.rateLimited;
  if (code === "invite_invalid") return t.invalid;
  if (code === "provision_failed") return t.provisionFailed;
  return t.failed;
}

export default function Signup() {
  const [locale, setLocale] = useState<Locale>(initialLocale);
  const [code, setCode] = useState("");
  const [email, setEmail] = useState("");
  const [orgName, setOrgName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<SignupResult | null>(null);
  const t = COPY[locale];

  useEffect(() => {
    document.documentElement.lang = locale;
    localStorage.setItem("baryon-locale", locale);
  }, [locale]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!code.trim() || !email.trim() || !orgName.trim()) {
      setError(t.required);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/v1/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: code.trim(), email: email.trim(), org_name: orgName.trim() }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = body?.detail;
        const detailCode = detail && typeof detail === "object" ? String(detail.code ?? "") : "";
        setError(messageFor(response.status, detailCode, t));
        return;
      }
      const payload = body as SignupResult;
      // Remember the organization and user for the dev-mode role switcher. In
      // OIDC mode the API ignores these headers and trusts the token instead.
      localStorage.setItem("baryon-organization", payload.organization_id);
      localStorage.setItem("baryon-user", payload.email);
      if (import.meta.env.VITE_AUTH_MODE !== "oidc") localStorage.setItem("baryon-role", "admin");
      setResult(payload);
    } catch {
      setError(t.failed);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="signup-page">
      <section className="signup-card" aria-labelledby="signup-title">
        <header className="signup-brand">
          <img src="/mascots/tacit.svg" width="42" height="42" alt="" />
          <div>
            <p className="eyebrow">{t.eyebrow}</p>
            <h1 id="signup-title">{result ? t.successTitle : t.title}</h1>
          </div>
          <label className="locale-select signup-locale">
            <Globe2 size={15} />
            <span className="sr-only">{t.language}</span>
            <select aria-label={t.language} value={locale} onChange={event => setLocale(event.target.value as Locale)}>
              <option value="ko">한국어</option>
              <option value="en">English</option>
            </select>
          </label>
        </header>

        {result ? (
          <div className="signup-success" role="status" aria-live="polite">
            <MailCheck size={30} />
            <p>{result.already_redeemed ? t.already : result.email_sent ? t.successBody(result.email) : t.successNoMail}</p>
            <dl>
              <div><dt>{t.orgId}</dt><dd>{result.organization_id}</dd></div>
              <div><dt>{t.email}</dt><dd>{result.email}</dd></div>
            </dl>
            <a className="primary signup-login" href={result.login_url || "/"}>
              <ShieldCheck size={16} />{t.openLogin}
            </a>
            <a className="signup-back" href="/"><ArrowLeft size={14} />{t.backHome}</a>
          </div>
        ) : (
          <>
            <p className="signup-intro">{t.body}</p>
            <form className="signup-form" onSubmit={submit} noValidate>
              <label className="signup-field">
                <span>{t.code}</span>
                <input
                  name="code" value={code} onChange={event => setCode(event.target.value)}
                  placeholder={t.codeHint} autoComplete="one-time-code" autoCapitalize="characters" spellCheck={false} required
                />
                <small>{t.codeHint}</small>
              </label>
              <label className="signup-field">
                <span>{t.email}</span>
                <input
                  name="email" type="email" value={email} onChange={event => setEmail(event.target.value)}
                  placeholder="you@example.com" autoComplete="email" required
                />
                <small>{t.emailHint}</small>
              </label>
              <label className="signup-field">
                <span>{t.orgName}</span>
                <input
                  name="org_name" value={orgName} onChange={event => setOrgName(event.target.value)}
                  placeholder={t.orgNameHint} autoComplete="organization" required
                />
                <small>{t.orgNameHint}</small>
              </label>
              {error && <p className="signup-error" role="alert" aria-live="polite">{error}</p>}
              <button className="primary signup-submit" type="submit" disabled={busy}>
                {busy ? t.submitting : t.submit}
              </button>
            </form>
            <a className="signup-back" href="/"><ArrowLeft size={14} />{t.backHome}</a>
          </>
        )}
      </section>
    </main>
  );
}
