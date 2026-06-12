export interface SlideItem {
  videoId: string;
  mediaPath: string; // file:// absolute URL
  artist: string;
  venue: string;
  date: string;
  time: string;
  price: string;
  coordinates: [number, number]; // [lng, lat]
}

export interface SlideShowProps {
  slides: SlideItem[];
  clipOffset: number;      // seconds into each clip to start (default: 30)
  fadeOutDur: number;      // audio fade-out duration per slide (default: 2)
  introDur: number;        // intro duration in seconds (default: 3)
  outroDur: number;        // outro duration in seconds (default: 5)
  slideDur: number;        // per-slide duration in seconds (default: 7)
  youtubeFillerPath: string | null; // file:// path to MP3 for intro/outro, or null
  maptilerKey: string;
}
