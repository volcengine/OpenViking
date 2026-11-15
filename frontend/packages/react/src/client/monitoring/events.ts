import type { RenderEvents } from './events-render.js';
import type { ResourceEvents } from './events-resource.js';
import type { TransportEvents } from './events-transport.js';
import type { BrowserEvents } from './events-browser.js';
import type { ConsoleEvents } from './events-console.js';
import type { ViewsEvents } from './events-views.js';

export type OpenVikingMonitoringEventMap = RenderEvents &
  ResourceEvents &
  TransportEvents &
  BrowserEvents &
  ConsoleEvents &
  ViewsEvents;
