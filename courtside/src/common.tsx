import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { imageUrl, savedInvites, type Loaded } from "./api";

const enc = encodeURIComponent;
export const teamPath = (team: string) => `/t/${enc(team)}`;
export const matchPath = (team: string, key: string) => `${teamPath(team)}/m/${key.split("/").map(enc).join("/")}`;
export const playerPath = (team: string, number: number) => `${teamPath(team)}/p/${number}`;

/** Loading / error states shared by every page. */
export function Gate<T>({ res, children }: { res: Loaded<T>; children: (doc: T) => React.ReactNode }) {
  const { team } = useParams();
  if (res.state === "loading") return <p className="muted">Loading…</p>;
  if (res.state === "error") {
    if (res.status === 403) {
      const code = team ? savedInvites.get(team) : null;
      return (
        <p className="notice">
          {res.message || "You don't have access to this page."}{" "}
          {code ? <Link to={`/join/${code}`}>Ask for access</Link> : "Ask your team for the invite link."}
        </p>
      );
    }
    const text =
      res.status === 401
        ? "Please sign in to see this page."
        : res.status === 503
          ? "The site is being set up. Check back soon."
          : res.status === 404
            ? res.message && res.message !== "Not found"
              ? res.message
              : "Nothing has been published here yet."
            : `Something went wrong (${res.message}).`;
    return <p className="notice">{text}</p>;
  }
  return <>{children(res.doc)}</>;
}

export function Crest({ src, color, label, size = 40 }: { src: string | null; color?: string; label: string; size?: number }) {
  return src ? (
    <img className="crest" src={imageUrl(src)} alt="" width={size} height={size} />
  ) : (
    <span className="crest crest-blank" style={{ width: size, height: size, background: color || "#555" }}>
      {label.slice(0, 2).toUpperCase()}
    </span>
  );
}

/**
 * Dresses the page in a team's colours: its colour becomes the accent
 * (links, buttons, headings' marks) and its crest sits large and faint
 * behind everything. Undone when the page changes to one without a team.
 */
export function TeamTheme({ color, logo, icon }: { color?: string | null; logo?: string | null; icon?: string | null }) {
  useEffect(() => {
    const root = document.documentElement;
    if (color) root.style.setProperty("--team", color);
    return () => {
      root.style.removeProperty("--team");
    };
  }, [color]);
  // The team's badge as the tab's icon while on its pages; the site's
  // own comes back on leaving them.
  useEffect(() => {
    const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (!icon || !link) return;
    const before = { href: link.getAttribute("href"), type: link.getAttribute("type") };
    link.removeAttribute("type"); // the badge may be any image type
    link.href = imageUrl(icon);
    return () => {
      if (before.href) link.setAttribute("href", before.href);
      if (before.type) link.setAttribute("type", before.type);
    };
  }, [icon]);
  return (
    <div className="backdrop" aria-hidden="true">
      {logo && <img src={imageUrl(logo)} alt="" />}
    </div>
  );
}

/** Copies (or, on a phone, shares) the team's invite link. Shown to the
 *  team's approved people and the admin; joining still needs approval. */
export function InviteButton({ team, teamName }: { team: string; teamName: string }) {
  const [msg, setMsg] = useState<string | null>(null);
  async function invite() {
    setMsg(null);
    try {
      const r = await fetch(`/api/invite/${encodeURIComponent(team)}`, { credentials: "same-origin" });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
      const { code } = (await r.json()) as { code: string };
      const url = `${location.origin}/join/${code}`;
      if (navigator.share && matchMedia("(pointer: coarse)").matches) {
        await navigator.share({ title: teamName, text: `Join ${teamName} on Courtside VB`, url }).catch(() => {});
      } else {
        await navigator.clipboard.writeText(url);
        setMsg("Invite link copied");
      }
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }
  return (
    <span className="invite-btn">
      <button onClick={() => void invite()} title="Anyone you send it to can ask to join; the admin approves each request">
        🔗 Invite
      </button>
      {msg && <span className="muted small">{msg}</span>}
    </span>
  );
}
