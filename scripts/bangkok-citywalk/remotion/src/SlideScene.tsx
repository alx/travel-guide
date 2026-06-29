import {AbsoluteFill, Img, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {WalkSlide} from './types';

export const PHOTO_HEIGHT = 760;
export const CARD_HEIGHT = 200;

const CROSSFADE_FRAMES = 9; // 0.3 s at 30 fps

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

  const sceneOpacity = interpolate(
    frame,
    [0, fadeInFrames, slideDurFrames - fadeOutFrames, slideDurFrames],
    [0, 1, 1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  const photos = slide.photos;
  const n = photos.length;

  // Determine which two photos are visible and the cross-fade amount
  let currentIdx = 0;
  let nextIdx = 0;
  let crossfadeT = 0;

  if (n > 1) {
    const sliceFrames = slideDurFrames / n;
    currentIdx = Math.min(Math.floor(frame / sliceFrames), n - 1);
    const localFrame = frame - currentIdx * sliceFrames;
    nextIdx = Math.min(currentIdx + 1, n - 1);
    if (nextIdx !== currentIdx && localFrame >= sliceFrames - CROSSFADE_FRAMES) {
      crossfadeT = (localFrame - (sliceFrames - CROSSFADE_FRAMES)) / CROSSFADE_FRAMES;
    }
  }

  const currentPhoto = n > 0 ? photos[currentIdx] : null;
  const nextPhoto = crossfadeT > 0 && nextIdx !== currentIdx ? photos[nextIdx] : null;

  return (
    <AbsoluteFill style={{opacity: sceneOpacity}}>
      {/* Photo area */}
      <div style={{position: 'absolute', top: 0, left: 0, right: 0, height: PHOTO_HEIGHT, background: '#111', overflow: 'hidden'}}>
        {/* Current photo */}
        {currentPhoto ? (
          <Img
            src={currentPhoto}
            style={{
              position: 'absolute', inset: 0,
              width: '100%', height: '100%', objectFit: 'contain',
              opacity: 1 - crossfadeT,
            }}
          />
        ) : (
          <div style={{width: '100%', height: '100%', background: '#1a1a2e'}} />
        )}

        {/* Next photo (cross-fade in) */}
        {nextPhoto && (
          <Img
            src={nextPhoto}
            style={{
              position: 'absolute', inset: 0,
              width: '100%', height: '100%', objectFit: 'contain',
              opacity: crossfadeT,
            }}
          />
        )}

        {/* Order badge */}
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
            fontFamily: "'Courier New', Courier, monospace",
            fontSize: 44,
            fontWeight: 900,
            color: '#fff',
            lineHeight: 1.15,
          }}
        >
          {slide.name}
        </div>
      </div>
    </AbsoluteFill>
  );
};
