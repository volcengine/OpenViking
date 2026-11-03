// Shared interaction contract for Docs (Vue) and Blog (React).
// Native details remains usable before hydration; fine-pointer hover is additive.
export function bindLanguageMenu(root) {
  const trigger = root.querySelector('summary')
  let timer
  let hoverOpened = false
  const clear = () => clearTimeout(timer)
  const close = () => { clear(); root.open = false; hoverOpened = false }
  const enter = event => {
    clear()
    if (event.pointerType !== 'mouse' || !matchMedia('(hover: hover) and (pointer: fine)').matches || root.open) return
    timer = setTimeout(() => { root.open = true; hoverOpened = true }, 120)
  }
  const leave = () => {
    clear()
    timer = setTimeout(() => { if (!root.contains(document.activeElement)) close() }, 250)
  }
  const click = event => {
    clear()
    if (trigger.contains(event.target) && hoverOpened) { event.preventDefault(); hoverOpened = false }
  }
  const key = event => {
    if (event.key === 'Escape' && root.open) {
      event.preventDefault(); event.stopPropagation(); close(); trigger.focus(); return
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' '].includes(event.key)) return
    if (['Enter', ' '].includes(event.key) && document.activeElement !== trigger) return
    const options = [...root.querySelectorAll('button')]
    const index = options.indexOf(document.activeElement)
    if (document.activeElement !== trigger && index < 0) return
    event.preventDefault(); clear(); root.open = true; hoverOpened = false
    const selected = options.findIndex(item => item.getAttribute('aria-pressed') === 'true')
    const next = ['Enter', ' '].includes(event.key) ? Math.max(0, selected) : event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
      : index < 0 ? (event.key === 'ArrowUp' ? options.length - 1 : 0)
      : (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length
    options[next]?.focus()
  }
  const blur = event => { if (!root.contains(event.relatedTarget)) close() }
  const outside = event => { if (!root.contains(event.target)) close() }
  const toggle = () => { trigger.setAttribute('aria-expanded', String(root.open)); if (!root.open) hoverOpened = false }
  const events = { pointerenter: enter, pointerleave: leave, click, keydown: key, focusout: blur, toggle }
  for (const [name, fn] of Object.entries(events)) root.addEventListener(name, fn)
  document.addEventListener('pointerdown', outside)
  toggle()
  return () => {
    clear()
    for (const [name, fn] of Object.entries(events)) root.removeEventListener(name, fn)
    document.removeEventListener('pointerdown', outside)
  }
}
