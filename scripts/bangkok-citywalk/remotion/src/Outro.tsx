import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

const MONO: React.CSSProperties = {fontFamily: "'Courier New', Courier, monospace"};

export const Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();

  const opacity = interpolate(
    frame,
    [0, Math.round(0.5 * fps), durationInFrames - Math.round(fps), durationInFrames],
    [0, 1, 1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  return (
    <AbsoluteFill
      style={{
        background: '#0a0a14',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        opacity,
      }}
    >
      <div style={{...MONO, fontSize: 52, fontWeight: 700, color: '#fff', letterSpacing: '0.02em', marginBottom: 16}}>
        BANGKOK
      </div>
      <div style={{width: 48, height: 3, background: '#FF6B35', borderRadius: 2, marginBottom: 16}} />
      <div style={{...MONO, fontSize: 16, color: '#555', letterSpacing: '0.08em'}}>
        maps.girard-davila.net
      </div>
    </AbsoluteFill>
  );
};
