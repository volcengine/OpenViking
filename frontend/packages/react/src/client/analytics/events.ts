import { browserEvents } from './browser-events.js';
import { searchEvents } from './search-events.js';
import { mutationEvents } from './mutation-events.js';
import { dialogEvents } from './dialog-events.js';
import { previewEvents } from './preview-events.js';
import { editingEvents } from './editing-events.js';
import { consoleEvents } from './console-events.js';
import { chatEvents } from './chat-events.js';
import { connectionEvents } from './connection-events.js';
import { tourEvents } from './tour-events.js';
import { dashboardEvents } from './dashboard-events.js';
import { authEvents } from './auth-events.js';

/** Internal catalogue: one target and one occurrence per event. */
export const analyticsDefinitions = {
  ...browserEvents,
  ...searchEvents,
  ...mutationEvents,
  ...dialogEvents,
  ...previewEvents,
  ...editingEvents,
  ...consoleEvents,
  ...chatEvents,
  ...connectionEvents,
  ...tourEvents,
  ...dashboardEvents,
  ...authEvents,
} as const;
