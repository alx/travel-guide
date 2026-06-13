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
import {SlideItem} from './types';

export const MAP_HEIGHT = 960;
const VENUE_ZOOM = 16;
const OVERVIEW_ZOOM = 12;
const TRANSITION_ZOOM = 14;
// Shifts logical center to 25% from top: (960 - 480) / 2 = 240px from top
const PADDING_BOTTOM = 480;
const MAP_PADDING = {top: 0, right: 0, bottom: PADDING_BOTTOM, left: 0};

interface Props {
  slides: SlideItem[];
  introDur: number;
  slideDur: number;
  maptilerKey: string;
}

function centroid(coords: [number, number][]): [number, number] {
  const lng = coords.reduce((s, c) => s + c[0], 0) / coords.length;
  const lat = coords.reduce((s, c) => s + c[1], 0) / coords.length;
  return [lng, lat];
}

function buildVenueGeojson(
  coords: [number, number][],
  activeIdx: number,
): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: coords.map((coord, i) => ({
      type: 'Feature',
      geometry: {type: 'Point', coordinates: coord},
      properties: {index: i, active: i === activeIdx},
    })),
  };
}

export const MapView: React.FC<Props> = ({slides, introDur, slideDur, maptilerKey}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const [map, setMap] = useState<maplibregl.Map | null>(null);
  const [loadHandle] = useState(() => delayRender('Loading MapLibre map'));

  const coords = slides.map(s => s.coordinates);
  const N = coords.length;
  const overviewCenter = N > 0 ? centroid(coords) : ([1.4442, 43.6047] as [number, number]);

  // Initialise map once.
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
      // GeoJSON source — all venues, none active initially (intro phase)
      mapInstance.addSource('venues', {
        type: 'geojson',
        data: buildVenueGeojson(coords, -1),
      });

      // Base circle: 5px inactive, 20px active — renders inside the WebGL canvas
      mapInstance.addLayer({
        id: 'venues-base',
        type: 'circle',
        source: 'venues',
        paint: {
          'circle-radius': ['case', ['==', ['get', 'active'], true], 20, 5],
          'circle-color': '#000000',
          'circle-stroke-width': 2,
          'circle-stroke-color': '#ffffff',
        },
      });

      // Glow halo for active venue — radius and opacity animated per frame
      mapInstance.addLayer({
        id: 'venues-glow',
        type: 'circle',
        source: 'venues',
        filter: ['==', ['get', 'active'], true],
        paint: {
          'circle-radius': 28,
          'circle-color': 'rgba(255,255,255,0)',
          'circle-stroke-width': 0,
          'circle-opacity': 0.5,
          'circle-blur': 0.4,
        },
      });

      // Music note text symbol for active venue — renders inside WebGL canvas
      mapInstance.addLayer({
        id: 'venues-icon',
        type: 'symbol',
        source: 'venues',
        filter: ['==', ['get', 'active'], true],
        layout: {
          'text-field': '♪',
          'text-size': 16,
          'text-anchor': 'center',
          'text-allow-overlap': true,
        },
        paint: {'text-color': '#ffffff'},
      });

      mapInstance.setPadding(MAP_PADDING);
      setMap(mapInstance);
      continueRender(loadHandle);
    });

    return () => mapInstance.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Move camera and update marker styles every frame.
  useEffect(() => {
    if (!map || N === 0) return;

    const handle = delayRender('Moving camera');
    const TRANSITION_FRAMES = fps;
    const introFrames = introDur * fps;
    const slideDurFrames = slideDur * fps;

    let lng: number;
    let lat: number;
    let zoom: number;
    let activeIdx: number;

    if (frame < introFrames) {
      [lng, lat] = overviewCenter;
      zoom = OVERVIEW_ZOOM;
      activeIdx = -1;
    } else {
      const idx = Math.min(Math.floor((frame - introFrames) / slideDurFrames), N - 1);
      activeIdx = idx;
      const localFrame = frame - introFrames - idx * slideDurFrames;
      const rawT = Math.min(localFrame / TRANSITION_FRAMES, 1);
      const easedT = Easing.inOut(Easing.cubic)(rawT);

      if (idx === 0) {
        // Intro → first venue: pan from centroid + zoom 12→16.
        lng = overviewCenter[0] + (coords[0][0] - overviewCenter[0]) * easedT;
        lat = overviewCenter[1] + (coords[0][1] - overviewCenter[1]) * easedT;
        zoom = OVERVIEW_ZOOM + (VENUE_ZOOM - OVERVIEW_ZOOM) * easedT;
      } else {
        // Venue → venue: pan + zoom arc 16→14→16.
        const prev = idx - 1;
        lng = coords[prev][0] + (coords[idx][0] - coords[prev][0]) * easedT;
        lat = coords[prev][1] + (coords[idx][1] - coords[prev][1]) * easedT;
        zoom = VENUE_ZOOM - Math.sin(rawT * Math.PI) * (VENUE_ZOOM - TRANSITION_ZOOM);
      }
    }

    // Pass padding explicitly on every jumpTo — setPadding alone is not always honoured.
    map.jumpTo({center: [lng, lat], zoom, padding: MAP_PADDING});

    // Update which venue is active via GeoJSON source data
    const source = map.getSource('venues') as maplibregl.GeoJSONSource;
    source.setData(buildVenueGeojson(coords, activeIdx));

    // Animate glow for active marker
    if (activeIdx >= 0) {
      const glow = 4 + Math.sin((frame * Math.PI) / (fps * 0.5)) * 4; // 4–8
      map.setPaintProperty('venues-glow', 'circle-radius', 20 + glow);
      map.setPaintProperty('venues-glow', 'circle-opacity', 0.3 + glow * 0.04);
    }

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
