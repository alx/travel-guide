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
          'text-size': 14,
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
          'text-size': 14,
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
          'text-size': 14,
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

      // Progressive zoom-in after transition settles (VENUE_ZOOM → VENUE_ZOOM+2)
      const settledT = localFrame > TRANSITION_FRAMES
        ? Easing.out(Easing.quad)(
            Math.min((localFrame - TRANSITION_FRAMES) / (slideDurFrames - TRANSITION_FRAMES), 1),
          )
        : 0;

      if (idx === 0) {
        lng = overviewCenter[0] + (coords[0][0] - overviewCenter[0]) * easedT;
        lat = overviewCenter[1] + (coords[0][1] - overviewCenter[1]) * easedT;
        zoom = rawT < 1
          ? OVERVIEW_ZOOM + (VENUE_ZOOM - OVERVIEW_ZOOM) * easedT
          : VENUE_ZOOM + settledT * 2;
      } else {
        const prev = idx - 1;
        lng = coords[prev][0] + (coords[idx][0] - coords[prev][0]) * easedT;
        lat = coords[prev][1] + (coords[idx][1] - coords[prev][1]) * easedT;
        zoom = VENUE_ZOOM + settledT * 2;
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
