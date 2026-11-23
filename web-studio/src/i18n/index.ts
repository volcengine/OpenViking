import { initReactI18next } from 'react-i18next'
import i18n from './core'

// The framework-independent instance is also used by API and formatting helpers.
// Attach the React adapter once at the application entry point.
initReactI18next.init(i18n)

export default i18n
