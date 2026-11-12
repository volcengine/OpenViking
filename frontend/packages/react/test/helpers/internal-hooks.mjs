import { pathToFileURL } from 'node:url';
import { join } from 'node:path';

const output = process.env.OV_UI_TEST_HOOKS;
if (!output) throw new Error('Run private Hook tests with npm test.');
const browser = await import(pathToFileURL(join(output, 'file-browser.js')));
const consoleModule = await import(pathToFileURL(join(output, 'console.js')));
export const useFileBrowser = browser.useFileBrowser;
export const useConsole = consoleModule.useConsole;
const provider = await import(pathToFileURL(join(output, 'provider.js')));
export const OpenVikingProvider = provider.OpenVikingProvider;
const wiki = await import(pathToFileURL(join(output, 'wiki-pane.js')));
export const WikiDashboardPane = wiki.WikiDashboardPane;
const experience = await import(
  pathToFileURL(join(output, 'experience-pane.js'))
);
export const ExperienceDashboardPane = experience.ExperienceDashboardPane;

const button = await import(pathToFileURL(join(output, 'button.js')));
export const Button = button.Button;
const input = await import(pathToFileURL(join(output, 'input.js')));
export const Input = input.Input;
export const TextArea = input.TextArea;
const modal = await import(pathToFileURL(join(output, 'modal.js')));
export const Modal = modal.Modal;
const entries = await import(pathToFileURL(join(output, 'browser-entries.js')));
export const FileBrowserActionBar = entries.FileBrowserActionBar;
