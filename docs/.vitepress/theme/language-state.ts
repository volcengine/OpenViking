import { createLanguagePreference } from './language-preference.js'

// Desktop and mobile menus observe the same in-page choice when storage is blocked.
export const docsLanguagePreference = createLanguagePreference()
