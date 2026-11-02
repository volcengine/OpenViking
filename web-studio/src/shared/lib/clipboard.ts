import i18n from '#/i18n/core'

export async function copyTextToClipboard(value: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(value)
    return
  } catch {
    // Clipboard API is unavailable on non-secure dev URLs such as http://10.x.x.x.
  }

  if (typeof document === 'undefined') {
    throw new Error(i18n.t('studio:errors.clipboardUnavailable'))
  }

  const textArea = document.createElement('textarea')
  textArea.value = value
  textArea.setAttribute('readonly', '')
  textArea.style.position = 'fixed'
  textArea.style.left = '-9999px'
  textArea.style.top = '0'
  document.body.appendChild(textArea)
  textArea.focus()
  textArea.select()

  try {
    const copied = document.execCommand('copy')
    if (!copied) {
      throw new Error(i18n.t('studio:errors.clipboardFailed'))
    }
  } finally {
    document.body.removeChild(textArea)
  }
}
