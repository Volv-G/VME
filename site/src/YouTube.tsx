import { useState } from "react";

/**
 * A YouTube video that loads only when asked: a season page holds dozens,
 * and a dozen live iframes would stall a phone. The privacy-enhanced
 * domain sets no cookies until the viewer presses play.
 */
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
    <button className="yt yt-poster" onClick={() => setPlaying(true)} aria-label={`Play ${title}`}>
      <img src={`https://i.ytimg.com/vi/${encodeURIComponent(id)}/hqdefault.jpg`} alt="" loading="lazy" />
      <span className="yt-play">▶</span>
      <span className="yt-title">{title}</span>
    </button>
  );
}
