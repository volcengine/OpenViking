import type { DirectoryTourIntroProps } from './types.js';
import { resolveLocale } from '../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

/** Presentational introduction; media playback and edit actions belong to the host. */
export function DirectoryTourIntro({
  locale,
  title,
  description,
  features,
  media,
  onDone,
  labels,
}: DirectoryTourIntroProps) {
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
        <button
          type="button"
          className="ov-directory-tour__primary"
          onClick={onDone}
        >
          {text.done}
        </button>
      </footer>
    </div>
  );
}
