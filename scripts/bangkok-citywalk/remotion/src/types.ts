export interface WalkSlide {
  name: string;
  order: number;           // 1-based POI index
  photoUrl: string;        // http://localhost:<port>/photos/<slug>.jpg
  attribution: string;
  coordinates: [number, number]; // [lng, lat]
}

export interface RouteSegment {
  coords: [number, number][]; // [lng, lat] pairs for one POI-to-POI leg
}

export interface WalkShowProps {
  slides: WalkSlide[];
  route: RouteSegment[];     // length = slides.length - 1 (one per consecutive POI pair)
  introDur: number;          // seconds, default 3
  outroDur: number;          // seconds, default 5
  slideDur: number;          // seconds per POI slide, default 10
  maptilerKey: string;
}
