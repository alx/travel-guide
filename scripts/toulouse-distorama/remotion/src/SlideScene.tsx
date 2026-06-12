import {AbsoluteFill, interpolate, OffthreadVideo, useCurrentFrame, useVideoConfig} from 'remotion';
import {SlideItem} from './types';

// Layout constants (px) — must match SlideShow.tsx.
export const VIDEO_HEIGHT = 600;
export const CARD_HEIGHT = 360;

interface Props {
  slide: SlideItem;
  clipOffset: number;
  fadeOutDur: number;
  slideDur: number;
}

const MONO: React.CSSProperties = {fontFamily: "'Courier New', Courier, monospace"};

export const SlideScene: React.FC<Props> = ({slide, clipOffset, fadeOutDur, slideDur}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const slideDurFrames = Math.round(slideDur * fps);
  const fadeOutFrames = Math.round(fadeOutDur * fps);
  const fadeInFrames = Math.round(0.4 * fps);

  // Fade the whole scene in/out.
  const opacity = interpolate(
    frame,
    [0, fadeInFrames, slideDurFrames - fadeOutFrames, slideDurFrames],
    [0, 1, 1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  // Volume: fade in 1 s, fade out at end.
  const volume = (f: number) =>
    interpolate(
      f,
      [0, fps, slideDurFrames - fadeOutFrames, slideDurFrames],
      [0, 1, 1, 0],
      {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
    );

  return (
    <AbsoluteFill style={{opacity}}>
      {/* YouTube clip — top portion of the bottom half */}
      <div style={{position: 'absolute', top: 0, left: 0, right: 0, height: VIDEO_HEIGHT, background: '#000'}}>
        <OffthreadVideo
          src={slide.mediaPath}
          startFrom={Math.round(clipOffset * fps)}
          volume={volume}
          style={{width: '100%', height: '100%', objectFit: 'cover'}}
        />
      </div>

      {/* Event card */}
      <div
        style={{
          position: 'absolute',
          top: VIDEO_HEIGHT,
          left: 0,
          right: 0,
          height: CARD_HEIGHT,
          background: '#0a0a0a',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          padding: '0 40px',
          gap: 0,
        }}
      >
        <div
          style={{
            ...MONO,
            fontSize: 20,
            fontWeight: 700,
            color: '#666',
            textTransform: 'uppercase',
            letterSpacing: '0.07em',
            marginBottom: 12,
          }}
        >
          {slide.venue}
        </div>
        <div
          style={{
            ...MONO,
            fontSize: 30,
            fontWeight: 700,
            color: '#fff',
            lineHeight: 1.25,
            marginBottom: 20,
          }}
        >
          {slide.artist}
        </div>
        <div style={{display: 'flex', gap: 20, alignItems: 'baseline'}}>
          <span style={{...MONO, fontSize: 18, color: '#888'}}>{slide.date}</span>
          {slide.time && <span style={{...MONO, fontSize: 18, color: '#888'}}>{slide.time}</span>}
          {slide.price && <span style={{...MONO, fontSize: 16, color: '#555'}}>{slide.price}</span>}
        </div>
      </div>
    </AbsoluteFill>
  );
};
