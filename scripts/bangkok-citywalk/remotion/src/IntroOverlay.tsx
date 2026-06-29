import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {MAP_HEIGHT} from './MapView';

interface Props {
  introDur: number;
}

const HIGHLIGHT: React.CSSProperties = {
  display: 'inline-block',
  background: '#FF6B35',
  color: '#fff',
  fontSize: 72,
  fontWeight: 900,
  letterSpacing: '0.06em',
  padding: '6px 28px',
};

export const IntroOverlay: React.FC<Props> = ({introDur}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const introFrames = Math.round(introDur * fps);

  const opacity = interpolate(
    frame,
    [
      0,
      Math.round(fps * 0.3),
      Math.round(introFrames * 0.7),
      introFrames,
    ],
    [0, 1, 1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  return (
    <AbsoluteFill
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        paddingTop: MAP_HEIGHT,
        opacity,
        pointerEvents: 'none',
        gap: 10,
      }}
    >
      <div style={HIGHLIGHT}>BANGKOK</div>
      <div style={HIGHLIGHT}>CITY WALK</div>
    </AbsoluteFill>
  );
};
