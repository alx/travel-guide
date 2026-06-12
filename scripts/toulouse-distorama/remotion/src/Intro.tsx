import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {SlideItem} from './types';

const MONO: React.CSSProperties = {fontFamily: "'Courier New', Courier, monospace"};

interface Props {
  slides: SlideItem[];
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
        background: '#0a0a0a',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        padding: '0 40px',
        opacity,
      }}
    >
      <div style={{...MONO, fontSize: 52, fontWeight: 700, color: '#fff', letterSpacing: '0.04em', marginBottom: 12}}>
        DISTORAMA
      </div>
      <div style={{...MONO, fontSize: 18, color: '#666', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 32}}>
        Toulouse Underground
      </div>
      <div style={{...MONO, fontSize: 16, color: '#444'}}>
        {slides.length} concert{slides.length !== 1 ? 's' : ''} · cette semaine
      </div>
    </AbsoluteFill>
  );
};
