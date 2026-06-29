import {AbsoluteFill, interpolate, Sequence, useCurrentFrame} from 'remotion';
import {IntroOverlay} from './IntroOverlay';
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

      {/* Intro title overlay — full canvas, above everything */}
      {introFrames > 0 && (
        <Sequence from={0} durationInFrames={introFrames}>
          <IntroOverlay introDur={introDur} />
        </Sequence>
      )}
    </AbsoluteFill>
  );
};
