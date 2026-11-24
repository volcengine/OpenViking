import type { Tooltip } from '@codemirror/view';
import type { FilePreviewEditorAnnotation } from './types.js';

export const tooltipClass = 'ov-file-preview__annotation-tooltip';

export function annotationTooltip(
  pos: number,
  annotations: readonly FilePreviewEditorAnnotation[],
): Tooltip {
  return {
    pos,
    above: true,
    arrow: true,
    create(view) {
      const doc = view.dom.ownerDocument;
      const dom = doc.createElement('div');
      dom.className = tooltipClass;
      dom.setAttribute('role', 'tooltip');
      const content = doc.createElement('div');
      content.className = 'ov-file-preview__annotation-tooltip-content';
      for (const annotation of annotations) {
        const section = doc.createElement('section');
        const description = doc.createElement('strong');
        description.textContent = annotation.description;
        section.append(description);
        if (annotation.reason) {
          const reason = doc.createElement('p');
          reason.textContent = annotation.reason;
          section.append(reason);
        }
        content.append(section);
      }
      dom.append(content);
      return { dom };
    },
  };
}
