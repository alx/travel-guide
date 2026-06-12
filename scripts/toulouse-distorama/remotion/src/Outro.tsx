import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

const MONO: React.CSSProperties = {fontFamily: "'Courier New', Courier, monospace"};

export const Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();

  // durationInFrames here is the Sequence duration (outroDur * fps), not the total.
  const opacity = interpolate(
    frame,
    [0, Math.round(0.5 * fps), durationInFrames - Math.round(fps), durationInFrames],
    [0, 1, 1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  return (
    <AbsoluteFill
      style={{
        background: '#0a0a0a',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        opacity,
      }}
    >
      <div style={{...MONO, fontSize: 52, fontWeight: 700, color: '#fff', letterSpacing: '0.04em', marginBottom: 16}}>
        DISTORAMA
      </div>
      <div style={{...MONO, fontSize: 18, color: '#555', letterSpacing: '0.08em'}}>
        distorama.fr
      </div>
    </AbsoluteFill>
  );
};
