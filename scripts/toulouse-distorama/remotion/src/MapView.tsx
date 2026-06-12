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

interface Props {
  slides: SlideItem[];
  introDur: number;
  slideDur: number;
  maptilerKey: string;
}

// Returns the index of the slide currently active (0-based).
// During intro: 0. Clamped to N-1 during outro.
function activeSlideIndex(frame: number, fps: number, introDur: number, slideDur: number, N: number): number {
  const introFrames = introDur * fps;
  const slideDurFrames = slideDur * fps;
  if (frame < introFrames || N === 0) return 0;
  return Math.min(Math.floor((frame - introFrames) / slideDurFrames), N - 1);
}

export const MapView: React.FC<Props> = ({slides, introDur, slideDur, maptilerKey}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const markersRef   = useRef<HTMLDivElement[]>([]);
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const [map, setMap] = useState<maplibregl.Map | null>(null);
  const [loadHandle] = useState(() => delayRender('Loading MapLibre map'));

  const coords = slides.map(s => s.coordinates);
  const N = coords.length;

  // Initialise map once.
  useEffect(() => {
    if (!containerRef.current || N === 0) {
      continueRender(loadHandle);
      return;
    }

    // canvasContextAttributes is required by Remotion's renderer but not typed in all maplibre-gl versions.
    const mapOptions = {
      container: containerRef.current,
      style: `https://api.maptiler.com/maps/toner-v2/style.json?key=${maptilerKey}`,
      center: coords[0],
      zoom: 14,
      interactive: false,
      attributionControl: false,
      fadeDuration: 0,
      canvasContextAttributes: {preserveDrawingBuffer: true},
    } as maplibregl.MapOptions;
    const mapInstance = new maplibregl.Map(mapOptions);

    // Small dot marker at every venue; record element refs so styles can be updated per-frame.
    coords.forEach((coord, i) => {
      const el = document.createElement('div');
      el.style.cssText = 'width:10px;height:10px;background:#fff;border:2px solid #555;border-radius:50%;';
      markersRef.current[i] = el;
      new maplibregl.Marker({element: el}).setLngLat(coord).addTo(mapInstance);
    });

    mapInstance.once('idle', () => {
      setMap(mapInstance);
      continueRender(loadHandle);
    });

    return () => mapInstance.remove();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Move camera every frame and highlight the active venue marker.
  useEffect(() => {
    if (!map || N === 0) return;

    const handle = delayRender('Moving camera');

    const TRANSITION_FRAMES = fps; // 1-second pan-in transition at start of each slide
    const idx  = activeSlideIndex(frame, fps, introDur, slideDur, N);
    const prev = Math.max(0, idx - 1);

    let lng: number;
    let lat: number;

    if (N === 1) {
      [lng, lat] = coords[0];
    } else {
      const introFrames    = introDur * fps;
      const slideDurFrames = slideDur * fps;
      // localFrame: how far into the current slide (or intro) we are
      const localFrame = frame < introFrames
        ? frame
        : (frame - introFrames) - idx * slideDurFrames;
      const tFrac = Easing.inOut(Easing.cubic)(Math.min(localFrame / TRANSITION_FRAMES, 1));
      lng = coords[prev][0] + (coords[idx][0] - coords[prev][0]) * tFrac;
      lat = coords[prev][1] + (coords[idx][1] - coords[prev][1]) * tFrac;
    }

    map.jumpTo({center: [lng, lat], zoom: 14});

    // Update marker styles: active = large red dot, others = small white dot.
    markersRef.current.forEach((el, i) => {
      if (i === idx) {
        el.style.cssText =
          'width:18px;height:18px;background:#ff4444;border:3px solid #fff;border-radius:50%;box-shadow:0 0 8px rgba(0,0,0,0.6);';
      } else {
        el.style.cssText =
          'width:10px;height:10px;background:#fff;border:2px solid #555;border-radius:50%;';
      }
    });

    const onIdle = () => continueRender(handle);
    map.once('idle', onIdle);
    map.triggerRepaint();

    return () => {
      map.off('idle', onIdle);
      continueRender(handle);
    };
  }, [frame, map]);

  return <div ref={containerRef} style={{width, height}} />;
};
