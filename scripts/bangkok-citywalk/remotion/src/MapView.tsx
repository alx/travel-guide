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
const TRANSITION_ZOOM = 13;
const PADDING_BOTTOM = 480;
const MAP_PADDING = {top: 0, right: 0, bottom: PADDING_BOTTOM, left: 0};

// Bangkok city centre fallback
const BANGKOK_CENTER: [number, number] = [100.5018, 13.7563];

interface Props {
  slides: WalkSlide[];
  route: RouteSegment[];
  introDur: number;
  slideDur: number;
  maptilerKey: string;
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
      properties: {index: i, active: i === activeIdx, order: s.order},
    })),
  };
}

function buildRouteGeojson(segments: RouteSegment[], upTo: number): GeoJSON.FeatureCollection {
  // Stitch legs 0..upTo-1 into one walked MultiLineString
  // Remaining legs as another (upcoming)
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

export const MapView: React.FC<Props> = ({slides, route, introDur, slideDur, maptilerKey}) => {
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
      style: `https://api.maptiler.com/maps/toner-v2/style.json?key=${maptilerKey}`,
      center: overviewCenter,
      zoom: OVERVIEW_ZOOM,
      interactive: false,
      attributionControl: false,
      fadeDuration: 0,
      canvasContextAttributes: {preserveDrawingBuffer: true},
    } as maplibregl.MapOptions);

    mapInstance.once('idle', () => {
      // Route source (walked + upcoming MultiLineStrings)
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

      // POI markers source
      mapInstance.addSource('markers', {
        type: 'geojson',
        data: buildMarkerGeojson(slides, -1),
      });
      // Inactive markers: small circle
      mapInstance.addLayer({
        id: 'markers-base',
        type: 'circle',
        source: 'markers',
        filter: ['!=', ['get', 'active'], true],
        paint: {
          'circle-radius': 6,
          'circle-color': '#FF6B35',
          'circle-stroke-width': 2,
          'circle-stroke-color': '#fff',
          'circle-opacity': 0.7,
        },
      });
      // Active marker: large circle
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
      // Order number on active marker
      mapInstance.addLayer({
        id: 'markers-label',
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
      routeUpTo = idx; // legs 0..idx-1 are "walked"
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
        zoom = VENUE_ZOOM - Math.sin(rawT * Math.PI) * (VENUE_ZOOM - TRANSITION_ZOOM);
      }
    }

    map.jumpTo({center: [lng, lat], zoom, padding: MAP_PADDING});

    const markerSource = map.getSource('markers') as maplibregl.GeoJSONSource;
    markerSource.setData(buildMarkerGeojson(slides, activeIdx));

    const routeSource = map.getSource('route') as maplibregl.GeoJSONSource;
    routeSource.setData(buildRouteGeojson(route, routeUpTo));

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
