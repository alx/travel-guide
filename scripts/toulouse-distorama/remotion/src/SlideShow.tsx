import {AbsoluteFill, Audio, interpolate, Sequence, useVideoConfig} from 'remotion';
import {Intro} from './Intro';
import {MapView} from './MapView';
import {Outro} from './Outro';
import {SlideScene} from './SlideScene';
import {SlideShowProps} from './types';

const MAP_HEIGHT = 960;   // top half of 1920
const BOTTOM_HEIGHT = 960; // bottom half

export const SlideShow: React.FC<SlideShowProps> = (props) => {
  const {slides, introDur, outroDur, slideDur, clipOffset, fadeOutDur, youtubeFillerPath, maptilerKey} = props;
  const {fps} = useVideoConfig();

  const introFrames = Math.round(introDur * fps);
  const slideDurFrames = Math.round(slideDur * fps);
  const outroFrames = Math.round(outroDur * fps);
  const outroFrom = introFrames + slides.length * slideDurFrames;

  return (
    <AbsoluteFill style={{background: '#0a0a0a'}}>

      {/* ── Top half: MapLibre map (continuous across full duration) ── */}
      <div style={{position: 'absolute', top: 0, left: 0, right: 0, height: MAP_HEIGHT, overflow: 'hidden'}}>
        <MapView
          slides={slides}
          introDur={introDur}
          slideDur={slideDur}
          maptilerKey={maptilerKey}
        />
      </div>

      {/* ── Bottom half: intro / slides / outro ── */}
      <div style={{position: 'absolute', top: MAP_HEIGHT, left: 0, right: 0, height: BOTTOM_HEIGHT}}>

        <Sequence from={0} durationInFrames={introFrames}>
          <Intro slides={slides} />
        </Sequence>

        {slides.map((slide, i) => (
          <Sequence key={`${slide.videoId}-${i}`} from={introFrames + i * slideDurFrames} durationInFrames={slideDurFrames}>
            <SlideScene
              slide={slide}
              clipOffset={clipOffset}
              fadeOutDur={fadeOutDur}
              slideDur={slideDur}
            />
          </Sequence>
        ))}

        <Sequence from={outroFrom} durationInFrames={outroFrames}>
          <Outro />
        </Sequence>
      </div>

      {/* ── Filler audio: intro ── */}
      {youtubeFillerPath && (
        <Sequence from={0} durationInFrames={introFrames}>
          <Audio
            src={youtubeFillerPath}
            startFrom={Math.round(30 * fps)}
            volume={(f) =>
              interpolate(f, [0, fps], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})
            }
          />
        </Sequence>
      )}

      {/* ── Filler audio: outro (picks up where intro left off) ── */}
      {youtubeFillerPath && (
        <Sequence from={outroFrom} durationInFrames={outroFrames}>
          <Audio
            src={youtubeFillerPath}
            startFrom={Math.round((30 + introDur) * fps)}
            volume={(f) =>
              interpolate(
                f,
                [outroFrames - Math.round(fps), outroFrames],
                [1, 0],
                {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
              )
            }
          />
        </Sequence>
      )}
    </AbsoluteFill>
  );
};
