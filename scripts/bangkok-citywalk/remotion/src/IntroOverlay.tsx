import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

interface Props {
  introDur: number;
}

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
        alignItems: 'center',
        justifyContent: 'center',
        opacity,
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          fontSize: 72,
          fontWeight: 900,
          color: 'rgba(0,0,0,0.75)',
          WebkitTextStroke: '3px #fff',
          textAlign: 'center',
          letterSpacing: '0.04em',
          lineHeight: 1.1,
          padding: '0 48px',
          textShadow: '0 2px 16px rgba(0,0,0,0.4)',
        }}
      >
        BANGKOK{'\n'}CITY WALK
      </div>
    </AbsoluteFill>
  );
};
