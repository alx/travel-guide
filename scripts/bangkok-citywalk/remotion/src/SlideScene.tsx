import {AbsoluteFill, Img, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {WalkSlide} from './types';

export const PHOTO_HEIGHT = 600;
export const CARD_HEIGHT = 360;

const MONO: React.CSSProperties = {fontFamily: "'Courier New', Courier, monospace"};

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

  const opacity = interpolate(
    frame,
    [0, fadeInFrames, slideDurFrames - fadeOutFrames, slideDurFrames],
    [0, 1, 1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  return (
    <AbsoluteFill style={{opacity}}>
      {/* Photo */}
      <div style={{position: 'absolute', top: 0, left: 0, right: 0, height: PHOTO_HEIGHT, background: '#111', overflow: 'hidden'}}>
        {slide.photos[0] ? (
          <Img
            src={slide.photos[0]}
            style={{width: '100%', height: '100%', objectFit: 'cover'}}
          />
        ) : (
          <div style={{width: '100%', height: '100%', background: '#1a1a2e', display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
            <span style={{...MONO, color: '#333', fontSize: 48}}>🗺</span>
          </div>
        )}
        {/* Order badge overlay */}
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
            ...MONO,
            fontSize: 32,
            fontWeight: 700,
            color: '#fff',
            lineHeight: 1.2,
            marginBottom: 16,
          }}
        >
          {slide.name}
        </div>
      </div>
    </AbsoluteFill>
  );
};
