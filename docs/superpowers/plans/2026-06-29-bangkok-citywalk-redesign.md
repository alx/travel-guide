# Bangkok Citywalk Visual Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Remotion citywalk video to match lequartier's visual style: OpenFreeMap Positron tiles, labelled markers, per-POI walking rings, pan-only transitions with pedestrian icon, map-based intro with outlined title, 2× title, no attribution, and multi-photo carousel.

**Architecture:** All visual changes live in the Remotion components under `scripts/bangkok-citywalk/remotion/src/`. The data pipeline (`generate.py`) is updated to fetch up to 5 Wikimedia photos per venue, and `render-walk.js` is updated to wire the new `photos[]` array through to Remotion props. No new npm dependencies are needed.

**Tech Stack:** Remotion 4, React 18, MapLibre GL 4, TypeScript 5, Python 3.11+ (uv), OpenFreeMap Positron (no API key).

## Global Constraints

- No new npm dependencies beyond what's already in `package.json`.
- No API key for map tiles — use OpenFreeMap Positron only.
- `WalkSlide.photos` is `string[]` (1–5 HTTP URLs), replacing `photoUrl: string` and `attribution: string`.
- `maptilerKey` is removed from `WalkShowProps`, `SlideShow`, `MapView`, `Root.tsx`, and `render-walk.js`.
- All Remotion components are verified by running `npm run citywalk:studio` from the repo root and inspecting in-browser.
- Python changes verified by `uv run scripts/bangkok-citywalk/generate.py --dry-run`.
- `generate.py` detects old-format mediacache entries (`thumb_url` key) and treats them as missing, triggering a fresh fetch.
- Photo files saved as `<slug>-1.jpg`, `<slug>-2.jpg`, … `<slug>-N.jpg` (1-based).

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `remotion/src/types.ts` | Modify | `WalkSlide.photos[]`, remove `maptilerKey` from `WalkShowProps` |
| `remotion/src/Root.tsx` | Modify | Remove `maptilerKey` from `DEFAULT_PROPS` |
| `remotion/src/MapView.tsx` | Modify | OpenFreeMap tiles, marker labels, walking rings, pan-only zoom |
| `remotion/src/SlideShow.tsx` | Modify | Remove `maptilerKey` prop, add pedestrian icon overlay, swap `Intro` → `IntroOverlay` |
| `remotion/src/Intro.tsx` | Delete | Replaced by `IntroOverlay` |
| `remotion/src/IntroOverlay.tsx` | Create | Outlined title text fading over intro duration |
| `remotion/src/SlideScene.tsx` | Modify | `photos[]` carousel, 2× title, remove attribution |
| `scripts/bangkok-citywalk/generate.py` | Modify | Multi-photo fetch (up to 5), updated mediacache + GeoJSON schema |
| `scripts/bangkok-citywalk/render-walk.js` | Modify | Remove `maptilerKey`, map `photos[]` from GeoJSON |

---

## Task 1: Update shared types and Root defaults

**Files:**
- Modify: `scripts/bangkok-citywalk/remotion/src/types.ts`
- Modify: `scripts/bangkok-citywalk/remotion/src/Root.tsx`

**Interfaces:**
- Produces: `WalkSlide.photos: string[]` — consumed by Tasks 6, 7, 8
- Produces: `WalkShowProps` without `maptilerKey` — consumed by Tasks 2, 6

- [ ] **Step 1: Update types.ts**

Replace the entire file:

```ts
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
```

- [ ] **Step 2: Update Root.tsx**

Replace the file:

```tsx
import {Composition} from 'remotion';
import {SlideShow} from './SlideShow';
import {WalkShowProps} from './types';

const DEFAULT_PROPS: WalkShowProps = {
  slides: [],
  route: [],
  introDur: 3,
  outroDur: 5,
  slideDur: 10,
};

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="BangkokCityWalk"
      component={SlideShow as unknown as React.ComponentType<Record<string, unknown>>}
      durationInFrames={30 * 30}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={DEFAULT_PROPS}
      calculateMetadata={async ({props}) => {
        const {slides, introDur, outroDur, slideDur} = props as unknown as WalkShowProps;
        const fps = 30;
        const durationInFrames = Math.round(fps * (introDur + slides.length * slideDur + outroDur));
        return {durationInFrames, props};
      }}
    />
  );
};
```

- [ ] **Step 3: TypeScript compile check**

```bash
cd /home/alx/code/travel-guide
npx tsc --project scripts/bangkok-citywalk/remotion/tsconfig.json --noEmit 2>&1 | head -30
```

Expected: errors only about `maptilerKey` still referenced in `MapView.tsx` and `SlideShow.tsx` (fixed in Task 2). Zero errors in `types.ts` or `Root.tsx`.

- [ ] **Step 4: Commit**

```bash
git add scripts/bangkok-citywalk/remotion/src/types.ts scripts/bangkok-citywalk/remotion/src/Root.tsx
git commit -m "feat(citywalk): remove maptilerKey, replace photoUrl with photos[]"
```

---

## Task 2: MapView — OpenFreeMap tiles, remove maptilerKey, add marker name labels

**Files:**
- Modify: `scripts/bangkok-citywalk/remotion/src/MapView.tsx`
- Modify: `scripts/bangkok-citywalk/remotion/src/SlideShow.tsx`

**Interfaces:**
- Consumes: `WalkShowProps` without `maptilerKey` (Task 1)
- Produces: MapView accepting no `maptilerKey` prop, Positron base tiles, name label symbol layer

- [ ] **Step 1: Replace MapView.tsx**

Write the full file. The only changes from the current version in this task are:
1. Remove `maptilerKey` from the `Props` interface and component signature
2. Change the map style URL to OpenFreeMap Positron
3. Add two symbol layers for marker name labels (active full opacity, inactive 0.6)

```tsx
import {useEffect, useRef, useState} from 'react';
import {
  continueRender,
  delayRender,
  Easing,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import {RouteSegment, WalkSlide} from './types';

export const MAP_HEIGHT = 960;
const VENUE_ZOOM = 15;
const OVERVIEW_ZOOM = 12;
const PADDING_BOTTOM = 480;
const MAP_PADDING = {top: 0, right: 0, bottom: PADDING_BOTTOM, left: 0};

const BANGKOK_CENTER: [number, number] = [100.5018, 13.7563];

const RING_CONFIGS = [
  {radiusM: 400, label: '5 min'},
  {radiusM: 800, label: '10 min'},
  {radiusM: 1200, label: '15 min'},
];

interface Props {
  slides: WalkSlide[];
  route: RouteSegment[];
  introDur: number;
  slideDur: number;
}

function centroid(coords: [number, number][]): [number, number] {
  const lng = coords.reduce((s, c) => s + c[0], 0) / coords.length;
  const lat = coords.reduce((s, c) => s + c[1], 0) / coords.length;
  return [lng, lat];
}

function buildMarkerGeojson(
  slides: WalkSlide[],
  activeIdx: number,
): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: slides.map((s, i) => ({
      type: 'Feature',
      geometry: {type: 'Point', coordinates: s.coordinates},
      properties: {index: i, active: i === activeIdx, order: s.order, name: s.name},
    })),
  };
}

function buildRouteGeojson(segments: RouteSegment[], upTo: number): GeoJSON.FeatureCollection {
  const walked = segments.slice(0, Math.max(0, upTo)).map(s => s.coords);
  const upcoming = segments.slice(Math.max(0, upTo)).map(s => s.coords);
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: {type: 'MultiLineString', coordinates: walked},
        properties: {role: 'walked'},
      },
      {
        type: 'Feature',
        geometry: {type: 'MultiLineString', coordinates: upcoming},
        properties: {role: 'upcoming'},
      },
    ],
  };
}

// Returns a 64-point GeoJSON Polygon approximating a circle of radiusM metres.
function makeCirclePolygon(
  center: [number, number],
  radiusM: number,
  steps = 64,
): GeoJSON.Feature<GeoJSON.Polygon> {
  const [lng, lat] = center;
  const coords: [number, number][] = [];
  for (let i = 0; i <= steps; i++) {
    const angle = (i / steps) * 2 * Math.PI;
    const dLat = (radiusM * Math.cos(angle)) / 111320;
    const dLng = (radiusM * Math.sin(angle)) / (111320 * Math.cos((lat * Math.PI) / 180));
    coords.push([lng + dLng, lat + dLat]);
  }
  return {
    type: 'Feature',
    geometry: {type: 'Polygon', coordinates: [coords]},
    properties: {},
  };
}

function buildRingsGeojson(center: [number, number] | null): GeoJSON.FeatureCollection {
  if (!center) return {type: 'FeatureCollection', features: []};
  return {
    type: 'FeatureCollection',
    features: RING_CONFIGS.map(({radiusM}) => makeCirclePolygon(center, radiusM)),
  };
}

function buildRingLabelsGeojson(center: [number, number] | null): GeoJSON.FeatureCollection {
  if (!center) return {type: 'FeatureCollection', features: []};
  return {
    type: 'FeatureCollection',
    features: RING_CONFIGS.map(({radiusM, label}) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [center[0], center[1] + radiusM / 111320],
      },
      properties: {label},
    })),
  };
}

export const MapView: React.FC<Props> = ({slides, route, introDur, slideDur}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const [map, setMap] = useState<maplibregl.Map | null>(null);
  const [loadHandle] = useState(() => delayRender('Loading MapLibre map'));

  const coords = slides.map(s => s.coordinates);
  const N = slides.length;
  const overviewCenter = N > 0 ? centroid(coords) : BANGKOK_CENTER;

  useEffect(() => {
    if (!containerRef.current || N === 0) {
      continueRender(loadHandle);
      return;
    }

    const mapInstance = new maplibregl.Map({
      container: containerRef.current,
      style: 'https://tiles.openfreemap.org/styles/positron',
      center: overviewCenter,
      zoom: OVERVIEW_ZOOM,
      interactive: false,
      attributionControl: false,
      fadeDuration: 0,
      canvasContextAttributes: {preserveDrawingBuffer: true},
    } as maplibregl.MapOptions);

    mapInstance.once('idle', () => {
      // Route
      mapInstance.addSource('route', {
        type: 'geojson',
        data: buildRouteGeojson(route, 0),
      });
      mapInstance.addLayer({
        id: 'route-upcoming',
        type: 'line',
        source: 'route',
        filter: ['==', ['get', 'role'], 'upcoming'],
        paint: {'line-color': '#444', 'line-width': 2, 'line-dasharray': [3, 2]},
      });
      mapInstance.addLayer({
        id: 'route-walked',
        type: 'line',
        source: 'route',
        filter: ['==', ['get', 'role'], 'walked'],
        paint: {'line-color': '#FF6B35', 'line-width': 3},
      });

      // Walking-distance rings
      mapInstance.addSource('rings', {
        type: 'geojson',
        data: buildRingsGeojson(null),
      });
      mapInstance.addLayer({
        id: 'rings-line',
        type: 'line',
        source: 'rings',
        paint: {
          'line-color': '#1a6b3c',
          'line-width': 1.5,
          'line-dasharray': [3, 2],
          'line-opacity': 0.7,
        },
      });
      mapInstance.addSource('ring-labels', {
        type: 'geojson',
        data: buildRingLabelsGeojson(null),
      });
      mapInstance.addLayer({
        id: 'rings-label',
        type: 'symbol',
        source: 'ring-labels',
        layout: {
          'text-field': ['get', 'label'],
          'text-size': 11,
          'text-anchor': 'bottom',
          'text-allow-overlap': true,
          'text-font': ['Noto Sans Bold'],
        },
        paint: {
          'text-color': '#1a6b3c',
          'text-halo-color': '#fff',
          'text-halo-width': 2,
        },
      });

      // POI markers
      mapInstance.addSource('markers', {
        type: 'geojson',
        data: buildMarkerGeojson(slides, -1),
      });
      mapInstance.addLayer({
        id: 'markers-base',
        type: 'circle',
        source: 'markers',
        filter: ['!=', ['get', 'active'], true],
        paint: {
          'circle-radius': 10,
          'circle-color': '#FF6B35',
          'circle-stroke-width': 2,
          'circle-stroke-color': '#fff',
          'circle-opacity': 0.7,
        },
      });
      mapInstance.addLayer({
        id: 'markers-active',
        type: 'circle',
        source: 'markers',
        filter: ['==', ['get', 'active'], true],
        paint: {
          'circle-radius': 20,
          'circle-color': '#FF6B35',
          'circle-stroke-width': 3,
          'circle-stroke-color': '#fff',
        },
      });
      // Number badge on active marker
      mapInstance.addLayer({
        id: 'markers-order',
        type: 'symbol',
        source: 'markers',
        filter: ['==', ['get', 'active'], true],
        layout: {
          'text-field': ['to-string', ['get', 'order']],
          'text-size': 14,
          'text-anchor': 'center',
          'text-allow-overlap': true,
          'text-font': ['Noto Sans Bold'],
        },
        paint: {'text-color': '#fff'},
      });
      // Name label below inactive markers (0.6 opacity)
      mapInstance.addLayer({
        id: 'markers-name-inactive',
        type: 'symbol',
        source: 'markers',
        filter: ['!=', ['get', 'active'], true],
        layout: {
          'text-field': ['get', 'name'],
          'text-size': 11,
          'text-anchor': 'top',
          'text-offset': [0, 1.2],
          'text-allow-overlap': false,
          'text-font': ['Noto Sans Regular'],
          'text-max-width': 8,
        },
        paint: {
          'text-color': '#1a1a1a',
          'text-halo-color': '#fff',
          'text-halo-width': 2,
          'text-opacity': 0.6,
        },
      });
      // Name label below active marker (full opacity)
      mapInstance.addLayer({
        id: 'markers-name-active',
        type: 'symbol',
        source: 'markers',
        filter: ['==', ['get', 'active'], true],
        layout: {
          'text-field': ['get', 'name'],
          'text-size': 11,
          'text-anchor': 'top',
          'text-offset': [0, 1.8],
          'text-allow-overlap': true,
          'text-font': ['Noto Sans Bold'],
          'text-max-width': 8,
        },
        paint: {
          'text-color': '#1a1a1a',
          'text-halo-color': '#fff',
          'text-halo-width': 2,
        },
      });

      mapInstance.setPadding(MAP_PADDING);
      setMap(mapInstance);
      continueRender(loadHandle);
    });

    return () => mapInstance.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!map || N === 0) return;

    const handle = delayRender('Moving camera');
    const TRANSITION_FRAMES = fps;
    const introFrames = Math.round(introDur * fps);
    const slideDurFrames = Math.round(slideDur * fps);

    let lng: number;
    let lat: number;
    let zoom: number;
    let activeIdx: number;
    let routeUpTo: number;

    if (frame < introFrames) {
      [lng, lat] = overviewCenter;
      zoom = OVERVIEW_ZOOM;
      activeIdx = -1;
      routeUpTo = 0;
    } else {
      const idx = Math.min(Math.floor((frame - introFrames) / slideDurFrames), N - 1);
      activeIdx = idx;
      routeUpTo = idx;
      const localFrame = frame - introFrames - idx * slideDurFrames;
      const rawT = Math.min(localFrame / TRANSITION_FRAMES, 1);
      const easedT = Easing.inOut(Easing.cubic)(rawT);

      if (idx === 0) {
        lng = overviewCenter[0] + (coords[0][0] - overviewCenter[0]) * easedT;
        lat = overviewCenter[1] + (coords[0][1] - overviewCenter[1]) * easedT;
        zoom = OVERVIEW_ZOOM + (VENUE_ZOOM - OVERVIEW_ZOOM) * easedT;
      } else {
        const prev = idx - 1;
        lng = coords[prev][0] + (coords[idx][0] - coords[prev][0]) * easedT;
        lat = coords[prev][1] + (coords[idx][1] - coords[prev][1]) * easedT;
        zoom = VENUE_ZOOM; // pan only — no zoom dip
      }
    }

    map.jumpTo({center: [lng, lat], zoom, padding: MAP_PADDING});

    const markerSource = map.getSource('markers') as maplibregl.GeoJSONSource;
    markerSource.setData(buildMarkerGeojson(slides, activeIdx));

    const routeSource = map.getSource('route') as maplibregl.GeoJSONSource;
    routeSource.setData(buildRouteGeojson(route, routeUpTo));

    // Walking-distance rings centred on active POI
    const activeCenter: [number, number] | null =
      activeIdx >= 0 ? coords[activeIdx] : null;
    (map.getSource('rings') as maplibregl.GeoJSONSource).setData(
      buildRingsGeojson(activeCenter),
    );
    (map.getSource('ring-labels') as maplibregl.GeoJSONSource).setData(
      buildRingLabelsGeojson(activeCenter),
    );

    const onIdle = () => continueRender(handle);
    map.once('idle', onIdle);
    map.triggerRepaint();

    return () => {
      map.off('idle', onIdle);
      continueRender(handle);
    };
  }, [frame, map]);

  return <div ref={containerRef} style={{width: 1080, height: MAP_HEIGHT}} />;
};
```

- [ ] **Step 2: Update SlideShow.tsx to remove maptilerKey**

Replace only the prop interface and the `<MapView>` call. The full SlideShow (without the pedestrian icon yet — that comes in Task 3) becomes:

```tsx
import {AbsoluteFill, Sequence} from 'remotion';
import {MAP_HEIGHT, MapView} from './MapView';
import {Outro} from './Outro';
import {SlideScene} from './SlideScene';
import {WalkShowProps} from './types';

const BOTTOM_HEIGHT = 960;

export const SlideShow: React.FC<WalkShowProps> = ({slides, route, introDur, outroDur, slideDur}) => {
  const fps = 30;
  const introFrames = Math.round(introDur * fps);
  const slideDurFrames = Math.round(slideDur * fps);
  const outroFrames = Math.round(outroDur * fps);
  const outroFrom = introFrames + slides.length * slideDurFrames;

  return (
    <AbsoluteFill style={{background: '#f5f5f0'}}>
      {/* Map */}
      <div style={{position: 'absolute', top: 0, left: 0, right: 0, height: MAP_HEIGHT, overflow: 'hidden'}}>
        <MapView
          slides={slides}
          route={route}
          introDur={introDur}
          slideDur={slideDur}
        />
      </div>

      {/* Bottom panel */}
      <div style={{position: 'absolute', top: MAP_HEIGHT, left: 0, right: 0, height: BOTTOM_HEIGHT}}>
        {slides.map((slide, i) => (
          <Sequence
            key={`poi-${slide.order}`}
            from={introFrames + i * slideDurFrames}
            durationInFrames={slideDurFrames}
          >
            <SlideScene slide={slide} slideDur={slideDur} />
          </Sequence>
        ))}

        {outroFrames > 0 && (
          <Sequence from={outroFrom} durationInFrames={outroFrames}>
            <Outro />
          </Sequence>
        )}
      </div>
    </AbsoluteFill>
  );
};
```

Note: `Intro` is intentionally omitted here — it's replaced by `IntroOverlay` in Task 4. The background changes from `#0a0a14` to `#f5f5f0` to complement the light map.

- [ ] **Step 3: TypeScript compile check**

```bash
npx tsc --project scripts/bangkok-citywalk/remotion/tsconfig.json --noEmit 2>&1 | head -30
```

Expected: zero errors.

- [ ] **Step 4: Visual check in Remotion Studio**

```bash
npm run citywalk:studio
```

Open `http://localhost:3000` in a browser. Verify:
- Map background is light grey/white (Positron), not dark.
- POI name labels appear as white-haloed text below each marker.
- Walking-distance rings (green dashed) appear around the active POI and move between stops.
- No TypeScript errors in the browser console.

- [ ] **Step 5: Commit**

```bash
git add scripts/bangkok-citywalk/remotion/src/MapView.tsx \
        scripts/bangkok-citywalk/remotion/src/SlideShow.tsx
git commit -m "feat(citywalk): OpenFreeMap Positron tiles, marker labels, walking rings, pan-only transitions"
```

---

## Task 3: Pedestrian icon overlay during transitions

**Files:**
- Modify: `scripts/bangkok-citywalk/remotion/src/SlideShow.tsx`

**Interfaces:**
- Consumes: `useCurrentFrame`, `interpolate` from Remotion; `introFrames`, `slideDurFrames`, `fps` computed locally
- Produces: A Font Awesome `fa-person-walking` icon div, centred over the map half, fading in/out during each POI-to-POI transition window

- [ ] **Step 1: Add pedestrian opacity helper and icon overlay to SlideShow.tsx**

Add this helper above the component, then add the icon `<div>` inside the `<AbsoluteFill>`:

```tsx
import {AbsoluteFill, interpolate, Sequence, useCurrentFrame} from 'remotion';
import {MAP_HEIGHT, MapView} from './MapView';
import {Outro} from './Outro';
import {SlideScene} from './SlideScene';
import {WalkShowProps} from './types';

const BOTTOM_HEIGHT = 960;
const TRANSITION_FRAMES = 30; // 1 s at 30 fps

function pedestrianOpacity(
  frame: number,
  introFrames: number,
  slideDurFrames: number,
  N: number,
): number {
  if (N <= 1) return 0;
  for (let i = 1; i < N; i++) {
    const slideStart = introFrames + i * slideDurFrames;
    const local = frame - slideStart;
    if (local >= 0 && local < TRANSITION_FRAMES) {
      return interpolate(
        local,
        [0, Math.round(TRANSITION_FRAMES * 0.1), Math.round(TRANSITION_FRAMES * 0.85), TRANSITION_FRAMES],
        [0, 1, 1, 0],
        {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
      );
    }
  }
  return 0;
}

export const SlideShow: React.FC<WalkShowProps> = ({slides, route, introDur, outroDur, slideDur}) => {
  const fps = 30;
  const introFrames = Math.round(introDur * fps);
  const slideDurFrames = Math.round(slideDur * fps);
  const outroFrames = Math.round(outroDur * fps);
  const outroFrom = introFrames + slides.length * slideDurFrames;
  const frame = useCurrentFrame();

  const iconOpacity = pedestrianOpacity(frame, introFrames, slideDurFrames, slides.length);

  return (
    <AbsoluteFill style={{background: '#f5f5f0'}}>
      {/* Map */}
      <div style={{position: 'absolute', top: 0, left: 0, right: 0, height: MAP_HEIGHT, overflow: 'hidden'}}>
        <MapView
          slides={slides}
          route={route}
          introDur={introDur}
          slideDur={slideDur}
        />
      </div>

      {/* Pedestrian icon — centred over the map half */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: MAP_HEIGHT,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          opacity: iconOpacity,
          pointerEvents: 'none',
        }}
      >
        <i
          className="fa-solid fa-person-walking"
          style={{
            fontSize: 64,
            color: '#fff',
            textShadow: '0 2px 8px rgba(0,0,0,0.6)',
          }}
        />
      </div>

      {/* Bottom panel */}
      <div style={{position: 'absolute', top: MAP_HEIGHT, left: 0, right: 0, height: BOTTOM_HEIGHT}}>
        {slides.map((slide, i) => (
          <Sequence
            key={`poi-${slide.order}`}
            from={introFrames + i * slideDurFrames}
            durationInFrames={slideDurFrames}
          >
            <SlideScene slide={slide} slideDur={slideDur} />
          </Sequence>
        ))}

        {outroFrames > 0 && (
          <Sequence from={outroFrom} durationInFrames={outroFrames}>
            <Outro />
          </Sequence>
        )}
      </div>
    </AbsoluteFill>
  );
};
```

- [ ] **Step 2: Add Font Awesome CDN to Remotion index**

Font Awesome must be loaded for the icon to render inside Remotion's Chromium. Check `remotion/src/index.tsx`:

```tsx
import {registerRoot} from 'remotion';
import {RemotionRoot} from './Root';

// Load Font Awesome for the pedestrian icon
const fa = document.createElement('link');
fa.rel = 'stylesheet';
fa.href = 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css';
document.head.appendChild(fa);

registerRoot(RemotionRoot);
```

- [ ] **Step 3: TypeScript compile check**

```bash
npx tsc --project scripts/bangkok-citywalk/remotion/tsconfig.json --noEmit 2>&1 | head -30
```

Expected: zero errors.

- [ ] **Step 4: Visual check**

```bash
npm run citywalk:studio
```

Scrub to the first transition between POI 1 and POI 2 (approximately frame `introFrames + slideDurFrames`). Verify the pedestrian icon (`fa-person-walking`) appears centred on the map, fades in, and fades out.

- [ ] **Step 5: Commit**

```bash
git add scripts/bangkok-citywalk/remotion/src/SlideShow.tsx \
        scripts/bangkok-citywalk/remotion/src/index.tsx
git commit -m "feat(citywalk): pedestrian icon overlay during POI transitions"
```

---

## Task 4: IntroOverlay — map-based intro with outlined title

**Files:**
- Create: `scripts/bangkok-citywalk/remotion/src/IntroOverlay.tsx`
- Modify: `scripts/bangkok-citywalk/remotion/src/SlideShow.tsx`
- Delete: `scripts/bangkok-citywalk/remotion/src/Intro.tsx`

**Interfaces:**
- Produces: `IntroOverlay` component accepting `introDur: number`; consumed by `SlideShow`

- [ ] **Step 1: Create IntroOverlay.tsx**

```tsx
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

interface Props {
  introDur: number;
}

export const IntroOverlay: React.FC<Props> = ({introDur}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const introFrames = Math.round(introDur * fps);

  const opacity = interpolate(
    frame,
    [
      0,
      Math.round(fps * 0.3),
      Math.round(introFrames * 0.7),
      introFrames,
    ],
    [0, 1, 1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  return (
    <AbsoluteFill
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        opacity,
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          fontSize: 72,
          fontWeight: 900,
          color: 'rgba(0,0,0,0.75)',
          WebkitTextStroke: '3px #fff',
          textAlign: 'center',
          letterSpacing: '0.04em',
          lineHeight: 1.1,
          padding: '0 48px',
          textShadow: '0 2px 16px rgba(0,0,0,0.4)',
        }}
      >
        BANGKOK{'\n'}CITY WALK
      </div>
    </AbsoluteFill>
  );
};
```

- [ ] **Step 2: Wire IntroOverlay into SlideShow.tsx and delete Intro.tsx**

Add the `IntroOverlay` `<Sequence>` inside the `<AbsoluteFill>` in `SlideShow.tsx`, overlaying the full canvas (not limited to map or bottom half):

```tsx
import {AbsoluteFill, interpolate, Sequence, useCurrentFrame} from 'remotion';
import {IntroOverlay} from './IntroOverlay';
import {MAP_HEIGHT, MapView} from './MapView';
import {Outro} from './Outro';
import {SlideScene} from './SlideScene';
import {WalkShowProps} from './types';

const BOTTOM_HEIGHT = 960;
const TRANSITION_FRAMES = 30;

function pedestrianOpacity(
  frame: number,
  introFrames: number,
  slideDurFrames: number,
  N: number,
): number {
  if (N <= 1) return 0;
  for (let i = 1; i < N; i++) {
    const slideStart = introFrames + i * slideDurFrames;
    const local = frame - slideStart;
    if (local >= 0 && local < TRANSITION_FRAMES) {
      return interpolate(
        local,
        [0, Math.round(TRANSITION_FRAMES * 0.1), Math.round(TRANSITION_FRAMES * 0.85), TRANSITION_FRAMES],
        [0, 1, 1, 0],
        {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
      );
    }
  }
  return 0;
}

export const SlideShow: React.FC<WalkShowProps> = ({slides, route, introDur, outroDur, slideDur}) => {
  const fps = 30;
  const introFrames = Math.round(introDur * fps);
  const slideDurFrames = Math.round(slideDur * fps);
  const outroFrames = Math.round(outroDur * fps);
  const outroFrom = introFrames + slides.length * slideDurFrames;
  const frame = useCurrentFrame();

  const iconOpacity = pedestrianOpacity(frame, introFrames, slideDurFrames, slides.length);

  return (
    <AbsoluteFill style={{background: '#f5f5f0'}}>
      {/* Map */}
      <div style={{position: 'absolute', top: 0, left: 0, right: 0, height: MAP_HEIGHT, overflow: 'hidden'}}>
        <MapView
          slides={slides}
          route={route}
          introDur={introDur}
          slideDur={slideDur}
        />
      </div>

      {/* Pedestrian icon */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: MAP_HEIGHT,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          opacity: iconOpacity,
          pointerEvents: 'none',
        }}
      >
        <i
          className="fa-solid fa-person-walking"
          style={{fontSize: 64, color: '#fff', textShadow: '0 2px 8px rgba(0,0,0,0.6)'}}
        />
      </div>

      {/* Bottom panel */}
      <div style={{position: 'absolute', top: MAP_HEIGHT, left: 0, right: 0, height: BOTTOM_HEIGHT}}>
        {slides.map((slide, i) => (
          <Sequence
            key={`poi-${slide.order}`}
            from={introFrames + i * slideDurFrames}
            durationInFrames={slideDurFrames}
          >
            <SlideScene slide={slide} slideDur={slideDur} />
          </Sequence>
        ))}

        {outroFrames > 0 && (
          <Sequence from={outroFrom} durationInFrames={outroFrames}>
            <Outro />
          </Sequence>
        )}
      </div>

      {/* Intro title overlay — full canvas, above everything */}
      {introFrames > 0 && (
        <Sequence from={0} durationInFrames={introFrames}>
          <IntroOverlay introDur={introDur} />
        </Sequence>
      )}
    </AbsoluteFill>
  );
};
```

Then delete the old intro file:

```bash
rm scripts/bangkok-citywalk/remotion/src/Intro.tsx
```

- [ ] **Step 3: TypeScript compile check**

```bash
npx tsc --project scripts/bangkok-citywalk/remotion/tsconfig.json --noEmit 2>&1 | head -30
```

Expected: zero errors.

- [ ] **Step 4: Visual check**

```bash
npm run citywalk:studio
```

Scrub to frame 0. Verify:
- The map is visible (Positron light style), zoomed out to city level.
- "BANGKOK CITY WALK" title appears in large outlined white-stroke text over the map.
- As you scrub forward, the title fades out and the map zooms toward the first POI.
- No black card is visible at any frame.

- [ ] **Step 5: Commit**

```bash
git add scripts/bangkok-citywalk/remotion/src/IntroOverlay.tsx \
        scripts/bangkok-citywalk/remotion/src/SlideShow.tsx
git rm scripts/bangkok-citywalk/remotion/src/Intro.tsx
git commit -m "feat(citywalk): map-based intro with outlined title overlay, remove black intro card"
```

---

## Task 5: SlideScene — multi-photo carousel, 2× title, remove attribution

**Files:**
- Modify: `scripts/bangkok-citywalk/remotion/src/SlideScene.tsx`

**Interfaces:**
- Consumes: `WalkSlide.photos: string[]` (Task 1)
- Produces: carousel with cross-fade between photos, title at 64 px/900 weight, no attribution

- [ ] **Step 1: Replace SlideScene.tsx**

```tsx
import {AbsoluteFill, Img, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {WalkSlide} from './types';

export const PHOTO_HEIGHT = 600;
export const CARD_HEIGHT = 360;

const CROSSFADE_FRAMES = 9; // 0.3 s at 30 fps

interface Props {
  slide: WalkSlide;
  slideDur: number;
}

export const SlideScene: React.FC<Props> = ({slide, slideDur}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const slideDurFrames = Math.round(slideDur * fps);
  const fadeInFrames = Math.round(0.4 * fps);
  const fadeOutFrames = Math.round(1.5 * fps);

  const sceneOpacity = interpolate(
    frame,
    [0, fadeInFrames, slideDurFrames - fadeOutFrames, slideDurFrames],
    [0, 1, 1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  const photos = slide.photos;
  const n = photos.length;

  // Determine which two photos are visible and the cross-fade amount
  let currentIdx = 0;
  let nextIdx = 0;
  let crossfadeT = 0;

  if (n > 1) {
    const sliceFrames = slideDurFrames / n;
    currentIdx = Math.min(Math.floor(frame / sliceFrames), n - 1);
    const localFrame = frame - currentIdx * sliceFrames;
    nextIdx = Math.min(currentIdx + 1, n - 1);
    if (nextIdx !== currentIdx && localFrame >= sliceFrames - CROSSFADE_FRAMES) {
      crossfadeT = (localFrame - (sliceFrames - CROSSFADE_FRAMES)) / CROSSFADE_FRAMES;
    }
  }

  const currentPhoto = n > 0 ? photos[currentIdx] : null;
  const nextPhoto = crossfadeT > 0 && nextIdx !== currentIdx ? photos[nextIdx] : null;

  return (
    <AbsoluteFill style={{opacity: sceneOpacity}}>
      {/* Photo area */}
      <div style={{position: 'absolute', top: 0, left: 0, right: 0, height: PHOTO_HEIGHT, background: '#111', overflow: 'hidden'}}>
        {/* Current photo */}
        {currentPhoto ? (
          <Img
            src={currentPhoto}
            style={{
              position: 'absolute', inset: 0,
              width: '100%', height: '100%', objectFit: 'cover',
              opacity: 1 - crossfadeT,
            }}
          />
        ) : (
          <div style={{width: '100%', height: '100%', background: '#1a1a2e'}} />
        )}

        {/* Next photo (cross-fade in) */}
        {nextPhoto && (
          <Img
            src={nextPhoto}
            style={{
              position: 'absolute', inset: 0,
              width: '100%', height: '100%', objectFit: 'cover',
              opacity: crossfadeT,
            }}
          />
        )}

        {/* Order badge */}
        <div
          style={{
            position: 'absolute', top: 20, left: 20,
            width: 44, height: 44, borderRadius: '50%',
            background: '#FF6B35', color: '#fff',
            fontSize: 20, fontWeight: 700,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: "'Courier New', Courier, monospace",
            boxShadow: '0 2px 12px rgba(0,0,0,0.6)',
          }}
        >
          {slide.order}
        </div>
      </div>

      {/* Info card */}
      <div
        style={{
          position: 'absolute',
          top: PHOTO_HEIGHT,
          left: 0, right: 0,
          height: CARD_HEIGHT,
          background: '#0a0a14',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          padding: '0 48px',
        }}
      >
        <div style={{width: 32, height: 3, background: '#FF6B35', borderRadius: 2, marginBottom: 20}} />
        <div
          style={{
            fontFamily: "'Courier New', Courier, monospace",
            fontSize: 64,
            fontWeight: 900,
            color: '#fff',
            lineHeight: 1.1,
          }}
        >
          {slide.name}
        </div>
      </div>
    </AbsoluteFill>
  );
};
```

- [ ] **Step 2: TypeScript compile check**

```bash
npx tsc --project scripts/bangkok-citywalk/remotion/tsconfig.json --noEmit 2>&1 | head -30
```

Expected: zero errors.

- [ ] **Step 3: Visual check**

```bash
npm run citywalk:studio
```

Scrub into a POI slide. Verify:
- Title is large (~64 px) and bold.
- No attribution text below title.
- If `slide.photos` has multiple entries, photos cross-fade during the slide.

- [ ] **Step 4: Commit**

```bash
git add scripts/bangkok-citywalk/remotion/src/SlideScene.tsx
git commit -m "feat(citywalk): multi-photo carousel, 2x title size, remove attribution"
```

---

## Task 6: generate.py — multi-photo fetch and updated GeoJSON schema

**Files:**
- Modify: `scripts/bangkok-citywalk/generate.py`

**Interfaces:**
- Produces:
  - `fetch_wikimedia_photos(name, max_results=5) → list[tuple[str, str]]`
  - Mediacache schema: `{"VenueName": {"photos": [{"url": "...", "attribution": "..."}]}}`
  - GeoJSON feature property: `"photos": ["/bangkok-citywalk/photos/slug-1.jpg", ...]`
  - Photo files saved as `<slug>-1.jpg`, `<slug>-2.jpg`, …

- [ ] **Step 1: Replace fetch_wikimedia_photo with fetch_wikimedia_photos**

In `generate.py`, replace the function `fetch_wikimedia_photo` with:

```python
def fetch_wikimedia_photos(name: str, max_results: int = 5) -> list[tuple[str, str]]:
    """
    Returns up to max_results (thumb_url, attribution) pairs from Wikimedia Commons.
    """
    params = urllib.parse.urlencode({
        "action": "query",
        "generator": "search",
        "gsrnamespace": 6,
        "gsrsearch": f"{name} Bangkok",
        "gsrlimit": max_results * 3,  # fetch extra to filter duds
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": 1080,
        "format": "json",
    })
    url = f"https://commons.wikimedia.org/w/api.php?{params}"
    results: list[tuple[str, str]] = []
    try:
        req = urllib.request.Request(url, headers=WIKIMEDIA_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            if len(results) >= max_results:
                break
            ii = page.get("imageinfo", [{}])[0]
            thumb = ii.get("thumburl", "")
            if not thumb:
                continue
            meta = ii.get("extmetadata", {})
            artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()
            license_name = meta.get("LicenseShortName", {}).get("value", "")
            attribution = f"© {artist} / {license_name}" if artist else license_name
            results.append((thumb, attribution))
    except Exception as e:
        print(f"  ⚠ Wikimedia error for '{name}': {e}", file=sys.stderr)
    return results
```

- [ ] **Step 2: Replace fetch_photos function**

Replace the old `fetch_photos` function:

```python
def fetch_photos(venues: list[dict], dry_run: bool) -> None:
    """Download up to 5 Wikimedia photos per venue. Saves as <slug>-1.jpg, <slug>-2.jpg, ..."""
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    cache = load_json(MEDIACACHE_PATH)
    updated = False

    for v in tqdm(venues, desc="Wikimedia photos", unit="venue"):
        name = v["name"]
        slug = slugify(name)

        # Detect old-format cache entry (has "thumb_url" key instead of "photos" list)
        cached = cache.get(name, {})
        is_new_format = isinstance(cached.get("photos"), list)

        # Check if all cached photos are already downloaded
        if is_new_format and cached["photos"]:
            all_present = all(
                (PHOTOS_DIR / f"{slug}-{i+1}.jpg").exists()
                for i in range(len(cached["photos"]))
            )
            if all_present:
                continue

        if dry_run:
            tqdm.write(f"  [dry-run] would fetch photos: {name}")
            continue

        results = fetch_wikimedia_photos(name, max_results=5)
        if not results:
            print(f"  ⚠ No photos found for: {name}", file=sys.stderr)
            cache[name] = {"photos": []}
            updated = True
            continue

        photo_entries = []
        for i, (thumb_url, attribution) in enumerate(results):
            dest = PHOTOS_DIR / f"{slug}-{i+1}.jpg"
            try:
                req = urllib.request.Request(thumb_url, headers=WIKIMEDIA_HEADERS)
                with urllib.request.urlopen(req, timeout=30) as r:
                    dest.write_bytes(r.read())
                photo_entries.append({"url": thumb_url, "attribution": attribution})
                tqdm.write(f"  → {name} [{i+1}]: {dest.name}")
            except Exception as e:
                print(f"  ⚠ Download failed for '{name}' photo {i+1}: {e}", file=sys.stderr)
            time.sleep(0.3)

        cache[name] = {"photos": photo_entries}
        updated = True
        time.sleep(0.5)

    if updated:
        save_json(MEDIACACHE_PATH, cache)
```

- [ ] **Step 3: Update write_geojson to emit photos array**

Replace the `write_geojson` function:

```python
def write_geojson(venues: list[dict], route_coords: list[list[float]], segment_breaks: list[int]) -> None:
    mediacache = load_json(MEDIACACHE_PATH)
    features = []

    for i, v in enumerate(venues):
        if not (v["lat"] and v["lng"]):
            continue
        slug = slugify(v["name"])
        cached = mediacache.get(v["name"], {})
        n_photos = len(cached.get("photos", []))
        photos = [
            f"/bangkok-citywalk/photos/{slug}-{j+1}.jpg"
            for j in range(n_photos)
        ]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(v["lng"]), float(v["lat"])]},
            "properties": {
                "name": v["name"],
                "order": i + 1,
                "slug": slug,
                "photos": photos,
            },
        })

    if route_coords:
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": route_coords},
            "properties": {
                "type": "route",
                "segment_breaks": segment_breaks,
            },
        })

    fc = {"type": "FeatureCollection", "features": features}
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    GEOJSON_OUT.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
    print(f"✓ {GEOJSON_OUT} — {len(features)} features")
```

- [ ] **Step 4: Dry-run verification**

```bash
uv run scripts/bangkok-citywalk/generate.py --dry-run
```

Expected output: lists each venue with `[dry-run] would fetch photos: <name>` and exits without writing files.

- [ ] **Step 5: Clear old mediacache and run for real**

The old `.mediacache.json` has single-photo entries. Delete it and re-fetch:

```bash
rm -f scripts/bangkok-citywalk/.mediacache.json
uv run scripts/bangkok-citywalk/generate.py
```

Expected: downloads up to 5 photos per venue (e.g. `wat-pho-1.jpg`, `wat-pho-2.jpg`, ...), prints `✓ static/bangkok-citywalk/walk.geojson`.

After completion, verify the GeoJSON:

```bash
python3 -c "
import json
data = json.load(open('static/bangkok-citywalk/walk.geojson'))
pois = [f for f in data['features'] if f['geometry']['type'] == 'Point']
for p in pois[:3]:
    print(p['properties']['name'], '->', p['properties']['photos'])
"
```

Expected: each POI shows a `photos` list with 1–5 entries like `['/bangkok-citywalk/photos/wat-pho-1.jpg', ...]`.

- [ ] **Step 6: Commit**

```bash
git add scripts/bangkok-citywalk/generate.py
git commit -m "feat(citywalk): fetch up to 5 Wikimedia photos per venue, update GeoJSON schema"
```

---

## Task 7: render-walk.js — remove maptilerKey, wire photos[]

**Files:**
- Modify: `scripts/bangkok-citywalk/render-walk.js`

**Interfaces:**
- Consumes: GeoJSON `feature.properties.photos: string[]` (Task 6)
- Produces: `WalkSlide.photos: string[]` in `inputProps` (matching Task 1 types)

- [ ] **Step 1: Update parseGeoJSON to build photos[]**

In `render-walk.js`, replace the `parseGeoJSON` function and remove the `maptilerKey` wiring:

```js
function parseGeoJSON(photoBaseUrl) {
  const data = JSON.parse(fs.readFileSync(GEOJSON, 'utf8'));
  const poiFeatures = data.features
    .filter(f => f.geometry.type === 'Point')
    .sort((a, b) => a.properties.order - b.properties.order);
  const routeFeature = data.features.find(f => f.geometry.type === 'LineString');

  const slides = poiFeatures.map(f => {
    const p = f.properties;
    const slug = p.slug;
    // Map GeoJSON /bangkok-citywalk/photos/<slug>-N.jpg paths to HTTP URLs
    const photos = (p.photos || [])
      .map(photoPath => {
        // Extract filename from path like /bangkok-citywalk/photos/slug-1.jpg
        const filename = photoPath.split('/').pop();
        const filePath = path.join(PHOTOS_DIR, filename);
        return fs.existsSync(filePath) ? `${photoBaseUrl}/${filename}` : null;
      })
      .filter(Boolean);
    return {
      name: p.name,
      order: p.order,
      photos,
      coordinates: f.geometry.coordinates,
    };
  });

  let routeSegments = [];
  if (routeFeature) {
    const allCoords = routeFeature.geometry.coordinates;
    const breaks = routeFeature.properties.segment_breaks;
    for (let i = 0; i < breaks.length - 1; i++) {
      const start = breaks[i];
      const end = (i + 1 < breaks.length) ? breaks[i + 1] : allCoords.length - 1;
      routeSegments.push({coords: allCoords.slice(start, end + 1)});
    }
  }

  return {slides, routeSegments};
}
```

- [ ] **Step 2: Remove maptilerKey from main()**

In the `main()` function, remove:
```js
const maptilerKey = env.MAPTILER_API_KEY || '';
if (!maptilerKey) console.warn('⚠ MAPTILER_API_KEY not set — map tiles may fail');
```

And update `inputProps` to remove the `maptilerKey` field:

```js
const inputProps = {
  slides,
  route: routeSegments,
  introDur: INTRO_DUR,
  outroDur: OUTRO_DUR,
  slideDur: SLIDE_DUR,
};
```

- [ ] **Step 3: Smoke-test render-walk.js data parsing**

```bash
node -e "
const fs = require('fs');
const path = require('path');
const GEOJSON = 'static/bangkok-citywalk/walk.geojson';
const PHOTOS_DIR = 'static/bangkok-citywalk/photos';
const data = JSON.parse(fs.readFileSync(GEOJSON, 'utf8'));
const pois = data.features.filter(f => f.geometry.type === 'Point');
const slide = pois[0];
console.log('First POI:', slide.properties.name);
console.log('photos[]:', slide.properties.photos);
"
```

Expected: prints the POI name and a list of photo paths.

- [ ] **Step 4: Full render smoke-test (short clip)**

```bash
node scripts/bangkok-citywalk/render-walk.js --intro-dur 2 --slide-dur 3 --outro-dur 2
```

Expected: renders a short video without errors. Check the console for `✓ static/bangkok-citywalk/bangkok-citywalk-<hash>.mp4`.

- [ ] **Step 5: Commit**

```bash
git add scripts/bangkok-citywalk/render-walk.js
git commit -m "feat(citywalk): remove maptilerKey from render pipeline, wire photos[] to slides"
```
