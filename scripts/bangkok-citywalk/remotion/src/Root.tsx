import {Composition} from 'remotion';
import {SlideShow} from './SlideShow';
import {WalkShowProps} from './types';

const DEFAULT_PROPS: WalkShowProps = {
  slides: [],
  route: [],
  introDur: 3,
  outroDur: 5,
  slideDur: 10,
  maptilerKey: '',
};

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="BangkokCityWalk"
      component={SlideShow as unknown as React.ComponentType<Record<string, unknown>>}
      durationInFrames={30 * 30}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={DEFAULT_PROPS}
      calculateMetadata={async ({props}) => {
        const {slides, introDur, outroDur, slideDur} = props as unknown as WalkShowProps;
        const fps = 30;
        const durationInFrames = Math.round(fps * (introDur + slides.length * slideDur + outroDur));
        return {durationInFrames, props};
      }}
    />
  );
};
