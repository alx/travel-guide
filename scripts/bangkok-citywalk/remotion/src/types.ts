export interface WalkSlide {
  name: string;
  order: number;
  photos: string[];              // 1–5 HTTP photo URLs; may be empty
  coordinates: [number, number]; // [lng, lat]
}

export interface RouteSegment {
  coords: [number, number][];
}

export interface WalkShowProps {
  slides: WalkSlide[];
  route: RouteSegment[];
  introDur: number;
  outroDur: number;
  slideDur: number;
}
