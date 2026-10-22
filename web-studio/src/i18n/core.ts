import i18n from 'i18next'
import LanguageDetector from 'i18next-browser-languagedetector'

import { defaultLanguage, resources, supportedLanguages } from './resources'

i18n.use(LanguageDetector).init({
  defaultNS: 'common',
  fallbackLng: defaultLanguage,
  interpolation: {
    escapeValue: false,
  },
  ns: Object.keys(resources[defaultLanguage]),
  resources,
  returnNull: false,
  supportedLngs: supportedLanguages,
})

export default i18n

function syncDocumentLanguage() {
  if (typeof document !== 'undefined') {
    document.documentElement.lang = i18n.resolvedLanguage ?? defaultLanguage
  }
}

i18n.on('languageChanged', syncDocumentLanguage)
syncDocumentLanguage()
