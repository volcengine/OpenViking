import type { ComponentPropsWithoutRef } from 'react';

import audio from '../../../icons/svg/audio.svg?inline';
import pdf from '../../../icons/svg/pdf.svg?inline';
import ppt from '../../../icons/svg/ppt.svg?inline';
import video from '../../../icons/svg/video.svg?inline';
import word from '../../../icons/svg/word.svg?inline';

type IconProps = Omit<ComponentPropsWithoutRef<'img'>, 'src' | 'srcSet'>;

function createIcon(src: string, name: string) {
  function Icon(props: IconProps) {
    return <img alt="" width={16} height={16} {...props} src={src} />;
  }

  Icon.displayName = name;
  return Icon;
}

export const AudioIcon = createIcon(audio, 'AudioIcon');
export const PdfIcon = createIcon(pdf, 'PdfIcon');
export const PptIcon = createIcon(ppt, 'PptIcon');
export const VideoIcon = createIcon(video, 'VideoIcon');
export const WordIcon = createIcon(word, 'WordIcon');
