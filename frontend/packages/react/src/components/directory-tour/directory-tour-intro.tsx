import { Button } from '../../shared/primitives/button/button.js';
import type { DirectoryTourIntroProps } from './types.js';
import { useLocale } from '../../shared/locale-context.js';
import { resolveLocale } from '../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

/** Presentational introduction; media playback and edit actions belong to the host. */
export function DirectoryTourIntro({
  locale: localeProp,
  title,
  description,
  features,
  media,
  onDone,
  labels,
}: DirectoryTourIntroProps) {
  const locale = useLocale(localeProp);
  const text = resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale, labels);
  return (
    <div className="ov-directory-tour__intro">
      {media && <div className="ov-directory-tour__media">{media}</div>}
      <div className="ov-directory-tour__intro-copy">
        <h3 className="ov-directory-tour__intro-title">{title}</h3>
        <div className="ov-directory-tour__intro-description">
          {description}
        </div>
      </div>
      {features?.length ? (
        <ul className="ov-directory-tour__features">
          {features.map((feature, index) => (
            <li key={index}>{feature}</li>
          ))}
        </ul>
      ) : null}
      <footer className="ov-directory-tour__actions">
        <Button
          size="small"
          type="primary"
          htmlType="button"
          className="ov-directory-tour__primary"
          onClick={onDone}
        >
          {text.done}
        </Button>
      </footer>
    </div>
  );
}
