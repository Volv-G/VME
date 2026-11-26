import { useState } from "react";
import type { Video } from "../shared/docs";
import { imageUrl } from "./api";

export const videoKey = (v: Video) => v.youtube_id || v.web || v.onedrive_url || v.onedrive_embed || "";

/**
 * A video tile. YouTube plays in place, loading only when asked (a season
 * page holds dozens, and a dozen live players would stall a phone) from
 * the privacy-enhanced domain, which sets no cookies until play.
 *
 * Reels play in place from their web copy on the site (server/videos.ts),
 * showing VME's thumbnail, with the full-HD original on OneDrive one
 * button away. A reel without a copy yet opens on OneDrive: its embedded
 * player shows a black box on a third-party page and it no longer serves
 * files to anonymous players. `poster` is an image key - an opponent's
 * crest, a player's photo - for tiles that have no thumbnail.
 *
 * `isNew` puts a "New" mark on the tile.
 */
export function VideoTile(props: {
  video: Video;
  title: string;
  poster?: string | null;
  /** Added since this person last opened the match. */
  isNew?: boolean;
}) {
  return (
    <div className="video-cell">
      <Tile {...props} />
      {props.isNew && (
        <span className="tile-marks">
          <span className="new-mark">New</span>
        </span>
      )}
    </div>
  );
}

function Tile({ video, title, poster }: { video: Video; title: string; poster?: string | null }) {
  if (video.youtube_id) return <YouTube id={video.youtube_id} title={title} />;
  const thumb = video.web_poster ? videoUrl(video.web_poster) : null;
  const original = video.onedrive_url || video.onedrive_embed;
  if (video.web) {
    return (
      <div className="video-item">
        <WebVideo src={videoUrl(video.web)} thumb={thumb} title={title} poster={poster} />
        {original && (
          <a className="download" href={original} target="_blank" rel="noreferrer">
            ⬇ Download full HD from OneDrive
          </a>
        )}
      </div>
    );
  }
  if (!original) return null;
  return (
    <a
      className={`yt yt-poster${thumb ? "" : " od-poster"}`}
      href={original}
      target="_blank"
      rel="noreferrer"
      aria-label={`Watch ${title} on OneDrive`}
    >
      <Picture thumb={thumb} poster={poster} />
      <span className="yt-play">▶</span>
      {thumb ? (
        // VME's thumbnail already carries the name; just say where it opens.
        <span className="od-corner">OneDrive ↗</span>
      ) : (
        <span className="yt-title">
          {title}
          <span className="od-badge">OneDrive ↗</span>
        </span>
      )}
    </a>
  );
}

const videoUrl = (key: string) => `/api/video/${key.split("/").map(encodeURIComponent).join("/")}`;

/** The tile's picture: the reel's own thumbnail, else a crest or photo on the team colour. */
function Picture({ thumb, poster }: { thumb: string | null; poster?: string | null }) {
  if (thumb) return <img src={thumb} alt="" loading="lazy" />;
  if (poster) return <img className="od-crest" src={imageUrl(poster)} alt="" loading="lazy" />;
  return null;
}

function WebVideo({
  src,
  thumb,
  title,
  poster,
}: {
  src: string;
  thumb: string | null;
  title: string;
  poster?: string | null;
}) {
  const [playing, setPlaying] = useState(false);
  if (playing) {
    return (
      <div className="yt">
        <video src={src} poster={thumb ?? undefined} title={title} controls autoPlay playsInline preload="auto" />
      </div>
    );
  }
  return (
    <button
      className={`yt yt-poster${thumb ? "" : " od-poster"}`}
      onClick={() => setPlaying(true)}
      aria-label={`Play ${title}`}
    >
      <Picture thumb={thumb} poster={poster} />
      <span className="yt-play">▶</span>
      {!thumb && <span className="yt-title">{title}</span>}
    </button>
  );
}

export function YouTube({ id, title }: { id: string; title: string }) {
  const [playing, setPlaying] = useState(false);
  if (playing) {
    return (
      <div className="yt">
        <iframe
          src={`https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?autoplay=1&rel=0`}
          title={title}
          allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
          allowFullScreen
        />
      </div>
    );
  }
  return (
    <button
      className="yt yt-poster"
      onClick={() => setPlaying(true)}
      aria-label={`Play ${title}`}
    >
      <img src={`https://i.ytimg.com/vi/${encodeURIComponent(id)}/hqdefault.jpg`} alt="" loading="lazy" />
      <span className="yt-play">▶</span>
      <span className="yt-title">{title}</span>
    </button>
  );
}

