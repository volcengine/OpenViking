export const tourEvents = {
  directory_tour_start: {
    zh: '引导首个步骤实际展示。',
    en: 'The first tour step becomes visible.',
    fields: { step_index: 'number' },
  },
  directory_tour_step_show: {
    zh: '当前引导步骤实际展示，不包含步骤正文。',
    en: 'The current tour step becomes visible; step content is omitted.',
    fields: { step_index: 'number' },
  },
  directory_tour_complete: {
    zh: '用户完成引导。',
    en: 'The user completes the tour.',
    fields: { step_index: 'number' },
  },
  directory_tour_close: {
    zh: '用户提前关闭引导。',
    en: 'The user closes the tour early.',
    fields: { step_index: 'number' },
  },
} as const;
