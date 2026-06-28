import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {WalkSlide} from './types';

const MONO: React.CSSProperties = {fontFamily: "'Courier New', Courier, monospace"};

interface Props {
  slides: WalkSlide[];
}

export const Intro: React.FC<Props> = ({slides}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const opacity = interpolate(frame, [0, Math.round(0.5 * fps)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        background: '#0a0a14',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        padding: '0 48px',
        opacity,
      }}
    >
      <div style={{...MONO, fontSize: 16, color: '#FF6B35', textTransform: 'uppercase', letterSpacing: '0.15em', marginBottom: 16}}>
        City Walk
      </div>
      <div style={{...MONO, fontSize: 52, fontWeight: 700, color: '#fff', letterSpacing: '0.02em', marginBottom: 12}}>
        BANGKOK
      </div>
      <div style={{...MONO, fontSize: 18, color: '#666', marginBottom: 32}}>
        {slides.length} landmarks · ~10 km
      </div>
      <div style={{width: 48, height: 3, background: '#FF6B35', borderRadius: 2}} />
    </AbsoluteFill>
  );
};
