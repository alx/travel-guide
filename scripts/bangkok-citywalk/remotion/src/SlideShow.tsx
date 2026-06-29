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
