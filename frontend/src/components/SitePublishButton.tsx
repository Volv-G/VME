import { useEffect, useState } from "react";
import { api } from "../api/client";

/**
 * Push the current scores, stats and YouTube videos to the public site.
 * It also happens by itself a minute after YouTube uploads finish; this
 * is for everything else - a corrected score, a new roster photo.
 * Hidden until the site is configured (`<data dir>/site.json`).
 */
export function SitePublishButton() {
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    void api.siteStatus().then((s) => setUrl(s.configured ? s.url : null)).catch(() => setUrl(null));
  }, []);

  if (!url) return null;

  async function publish() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.publishSite();
      setMsg(`Published ${r.docs} pages`);
    } catch (e) {
      setMsg(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {msg && <span className="row-meta">{msg}</span>}
      <button onClick={publish} disabled={busy} title={`Update ${url} with every team's scores, stats and videos`}>
        {busy ? "Publishing…" : "🌐 Publish to site"}
      </button>
    </>
  );
}
