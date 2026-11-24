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
