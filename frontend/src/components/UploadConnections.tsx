import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { AuthStatusDto } from "../types/api";

/**
 * Connect the server to the accounts it uploads with.
 *
 * This replaces running an authorize script on the box. The thing that
 * reliably breaks uploads is an expired refresh token, and until now
 * fixing it needed a shell on the server — which meant the person who
 * noticed usually could not be the person who fixed it.
 *
 * Lives under Server settings, because that is what it is: one Google
 * token authorises one channel and one Microsoft token one drive, for
 * everything this server does. It sat on the team page first and had
 * to carry a paragraph explaining that it was not per-team, which is
 * the sort of note that means the thing is in the wrong place.
 */
type Provider = "google" | "microsoft";

/**
 * Where consent should land the browser afterwards.
 *
 * `window.location.pathname`, deliberately — **not** a React Router
 * path. The app is served under a basename (`/vme` in production), so
 * router paths omit it; handing one to the server produced a redirect
 * to `/teams/.../settings` that IIS answered with a 404 because the
 * real page is at `/vme/teams/.../settings`. The browser's own
 * pathname is the only value that is right in both dev and production.
 */
function returnHere(): string {
  return window.location.pathname;
}

const PROVIDERS: { id: Provider; label: string; what: string }[] = [
  { id: "google", label: "Google / YouTube", what: "Matches, reels and live streams." },
  { id: "microsoft", label: "Microsoft / OneDrive", what: "Anything set to OneDrive." },
];

const MESSAGES: Record<string, string> = {
  ok: "Connected.",
  denied: "Consent was refused, so nothing changed.",
  expired:
    "That sign-in attempt timed out, or the server restarted mid-flow. Try again.",
  no_refresh_token:
    "No refresh token came back, so uploads would stop working within the hour. " +
    "Revoke this app's access in your account and connect again.",
  failed: "The sign-in did not complete. Check the server log for the reason.",
};

export function UploadConnections() {
  const [status, setStatus] = useState<Record<Provider, AuthStatusDto | null>>({
    google: null,
    microsoft: null,
  });
  const [busy, setBusy] = useState<Provider | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [google, microsoft] = await Promise.all([
        api.authStatus("google"),
        api.authStatus("microsoft"),
      ]);
      setStatus({ google, microsoft });
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Each callback redirects back here with its outcome in the query, so
  // a flow that left the app reports its result inside it.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    let touched = false;
    for (const key of ["google_auth", "microsoft_auth"]) {
      const result = params.get(key);
      if (!result) continue;
      touched = true;
      if (result === "ok") setInfo(MESSAGES.ok);
      else setErr(MESSAGES[result] ?? "Sign-in failed.");
      params.delete(key);
    }
    if (!touched) return;
    const q = params.toString();
    window.history.replaceState(
      {},
      "",
      window.location.pathname + (q ? `?${q}` : "")
    );
    void load();
  }, [load]);

  async function connect(provider: Provider) {
    setBusy(provider);
    setErr(null);
    try {
      const { auth_url } = await api.authStart(provider, returnHere());
      // A full navigation, not a popup: consent screens are blocked in
      // many embedded contexts, and a popup that is silently blocked
      // looks exactly like a button that does nothing.
      window.location.href = auth_url;
    } catch (e) {
      setErr(String(e));
      setBusy(null);
    }
  }

  async function disconnect(provider: Provider) {
    if (!window.confirm(`Disconnect ${provider}? Uploads to it will stop.`)) return;
    setBusy(provider);
    try {
      const next = await api.authDisconnect(provider);
      setStatus((s) => ({ ...s, [provider]: next }));
      setInfo("Disconnected.");
      setErr(null);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="card">
      <div className="card-header">
        <h2>Upload connections</h2>
      </div>

      {err && <div className="error">{err}</div>}
      {info && <div className="info">{info}</div>}

      <div className="list">
        {PROVIDERS.map(({ id, label, what }) => {
          const s = status[id];
          const needsWebClient = id === "google" && s?.client.kind === "installed";
          const canConnect = s?.client.present === true && !needsWebClient;
          return (
            <div key={id} className="list-row conn-row">
              <div className="conn-info">
                <strong>{label}</strong>
                {s?.connected && <span className="ok-badge">connected</span>}
                <div className="muted" style={{ fontSize: 12 }}>
                  {s ? s.reason || what : "…"}
                </div>
              </div>
              {s?.connected ? (
                <>
                  <button onClick={() => connect(id)} disabled={busy !== null || !canConnect}>
                    Reconnect
                  </button>
                  <button
                    className="danger"
                    onClick={() => disconnect(id)}
                    disabled={busy !== null}
                  >
                    Disconnect
                  </button>
                </>
              ) : (
                <button
                  className="primary"
                  onClick={() => connect(id)}
                  disabled={busy !== null || !canConnect}
                >
                  Connect
                </button>
              )}
            </div>
          );
        })}
      </div>

      {status.google && (!status.google.client.present ||
        status.google.client.kind === "installed") && (
        <GoogleSetup
          status={status.google}
          busy={busy !== null}
          onSaved={(next) => setStatus((s) => ({ ...s, google: next }))}
          onError={setErr}
        />
      )}

      {status.microsoft && !status.microsoft.client.present && (
        <MicrosoftSetup
          status={status.microsoft}
          busy={busy !== null}
          onSaved={(next) => setStatus((s) => ({ ...s, microsoft: next }))}
          onError={setErr}
        />
      )}
    </div>
  );
}

/** Google wants a downloaded JSON; Microsoft wants two strings. Hence
 *  two setup blocks rather than one generic one. */
function GoogleSetup({
  status,
  busy,
  onSaved,
  onError,
}: {
  status: AuthStatusDto;
  busy: boolean;
  onSaved: (s: AuthStatusDto) => void;
  onError: (m: string) => void;
}) {
  const isDesktop = status.client.kind === "installed";
  return (
    <div className="setup-block">
      <h3>Set up Google</h3>
      <p className="muted" style={{ fontSize: 12 }}>
        In Google Cloud Console create an OAuth client of type{" "}
        <strong>Web application</strong> and add this exact callback to its
        authorised redirect URIs:
      </p>
      <code className="redirect-uri">{status.redirect_uri}</code>
      <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
        Then download its JSON and upload it here.
        {isDesktop &&
          " The client currently saved is a Desktop one, which can only" +
            " redirect to localhost and so cannot be driven from a browser."}
      </p>
      <input
        type="file"
        accept="application/json,.json"
        disabled={busy}
        onChange={async (e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (!f) return;
          try {
            onSaved(await api.googleAuthClientSecret(f));
          } catch (ex) {
            onError(String(ex));
          }
        }}
      />
    </div>
  );
}

function MicrosoftSetup({
  status,
  busy,
  onSaved,
  onError,
}: {
  status: AuthStatusDto;
  busy: boolean;
  onSaved: (s: AuthStatusDto) => void;
  onError: (m: string) => void;
}) {
  const [id, setId] = useState("");
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);
  return (
    <div className="setup-block">
      <h3>Set up Microsoft</h3>
      <p className="muted" style={{ fontSize: 12 }}>
        In the Azure portal register an application, allow{" "}
        <em>personal Microsoft accounts</em>, add the delegated permissions{" "}
        <code>Files.ReadWrite</code> and <code>offline_access</code>, and add
        this redirect URI (platform: Web):
      </p>
      <code className="redirect-uri">{status.redirect_uri}</code>
      <div className="field-row" style={{ marginTop: 8 }}>
        <label style={{ flex: 2 }}>
          Application (client) ID
          <input value={id} onChange={(e) => setId(e.target.value)} />
        </label>
        <label style={{ flex: 2 }}>
          Client secret
          <input
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder="blank for a public client"
          />
        </label>
      </div>
      <button
        className="primary"
        disabled={busy || saving || !id.trim()}
        onClick={async () => {
          setSaving(true);
          try {
            onSaved(await api.microsoftClient(id.trim(), secret.trim()));
          } catch (ex) {
            onError(String(ex));
          } finally {
            setSaving(false);
          }
        }}
      >
        {saving ? "Saving…" : "Save app registration"}
      </button>
    </div>
  );
}
