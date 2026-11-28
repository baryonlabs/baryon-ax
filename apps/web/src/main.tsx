import { createRoot } from "react-dom/client";

import App from "./App";
import Signup from "./Signup";
import { initAuth } from "./auth";
import "./styles.css";

const root = createRoot(document.getElementById("root")!);
const path = window.location.pathname.replace(/\/+$/, "") || "/";

if (path === "/signup") {
  // Public route: never call initAuth here, because OIDC mode would redirect to
  // the IdP before the person could redeem an invitation code.
  root.render(<Signup />);
} else {
  // In dev mode initAuth resolves to null immediately; in OIDC mode it completes
  // the login callback (or redirects to the IdP) before the workspace renders.
  initAuth()
    .then(session => root.render(<App session={session} />))
    .catch((error: unknown) => {
      root.render(<main className="auth-error" role="alert"><h1>Sign-in failed</h1><p>{error instanceof Error ? error.message : String(error)}</p><button onClick={() => window.location.assign("/")}>Retry</button></main>);
    });
}
