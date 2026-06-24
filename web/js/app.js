/**
 * App bootstrap.
 *
 * Flow: probe the server session (GET /api/me) -> if signed in, mount the
 * role-gated shell; otherwise show sign-in. The session object (roles, committee,
 * team, …) is resolved authoritatively by the server, so there is no client-side
 * role resolution anymore.
 *
 * Sign-in is a full-page redirect to the API's Google OAuth; on return the
 * server may append #/signin?error=... which we surface here.
 */

import { getSession, signIn, signOut } from "./auth.js";
import { ALLOWED_AUTH_DOMAINS } from "./config.js";
import { mountShell, unmountShell } from "./shell.js";

const els = {
  loading: document.getElementById("loading"),
  signin: document.getElementById("signin-view"),
  app: document.getElementById("app-view"),
  signinBtn: document.getElementById("signin-btn"),
  signinError: document.getElementById("signin-error"),
};

function show(view) {
  els.loading.hidden = view !== "loading";
  els.signin.hidden = view !== "signin";
  els.app.hidden = view !== "app";
}

function showSigninError(msg) {
  els.signinError.textContent = msg;
  els.signinError.hidden = false;
}

/** Read (and clear) an ?error=... left in the hash by the OAuth callback. */
function consumeAuthError() {
  const m = location.hash.match(/[?&]error=([^&]+)/);
  if (!m) return null;
  // Strip the query part from the hash so a refresh doesn't keep showing it.
  history.replaceState(null, "", location.pathname + "#/");
  return decodeURIComponent(m[1]);
}

els.signinBtn.addEventListener("click", () => {
  els.signinError.hidden = true;
  els.signinBtn.disabled = true;
  signIn(); // full-page redirect; control leaves the app here
});

async function doSignOut() {
  try {
    await signOut();
  } catch {
    /* ignore — clearing the session below regardless */
  }
  unmountShell();
  els.app.innerHTML = "";
  location.hash = "#/";
  show("signin");
}

async function boot() {
  show("loading");

  const authError = consumeAuthError();
  if (authError === "domain") {
    show("signin");
    showSigninError(
      `Only ${ALLOWED_AUTH_DOMAINS.join(", ")} accounts can use this portal.`
    );
    return;
  }

  let session;
  try {
    session = await getSession();
  } catch (err) {
    show("signin");
    showSigninError(`Couldn't reach the server: ${err.message || err}`);
    return;
  }

  if (!session) {
    show("signin");
    if (authError === "failed") showSigninError("Sign-in failed. Please try again.");
    return;
  }

  try {
    mountShell(els.app, session, { onSignOut: doSignOut });
    show("app");
  } catch (err) {
    showSigninError(`Couldn't load your account: ${err?.message || err}`);
    show("signin");
  }
}

boot();
