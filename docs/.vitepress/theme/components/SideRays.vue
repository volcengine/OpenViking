<script setup>
// SideRays shader by React Bits (https://reactbits.dev/backgrounds/side-rays).
// Vue lifecycle adapter: one context, reactive uniforms, offscreen/hidden pause.
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import './SideRays.css'

const props = defineProps({
  speed: { default: 2.5 }, rayColor1: { default: '#EAB308' },
  rayColor2: { default: '#96c8ff' }, intensity: { default: 2 },
  spread: { default: 2 }, origin: { default: 'top-right' }, tilt: { default: 0 },
  saturation: { default: 1.5 }, blend: { default: 0.75 },
  falloff: { default: 1.6 }, opacity: { default: 1 }
})
const container = ref(null)
let dispose = () => {}
let cancelled = false
const rgb = hex => /^#?([a-f\d]{6})$/i.test(hex)
  ? hex.replace('#', '').match(/../g).map(v => parseInt(v, 16) / 255) : [1, 1, 1]
let sync = () => {}
watch(props, () => sync())
onBeforeUnmount(() => { cancelled = true; dispose() })
onMounted(async () => {
  // OGL never enters the server renderer or the article-page bundle.
  const { Renderer, Program, Triangle, Mesh } = await import('ogl')
  if (cancelled || !container.value) return
  const el = container.value
  let renderer
  try { renderer = new Renderer({ dpr: Math.min(devicePixelRatio, 2), alpha: true }) }
  catch { return } // The CSS glow remains when WebGL is unavailable.
  const gl = renderer.gl
  if (!gl) return
  const uniforms = {
    iTime: { value: 0 }, iResolution: { value: [1, 1] },
    ...Object.fromEntries(['Speed', 'RayColor1', 'RayColor2', 'Intensity', 'Spread', 'FlipX', 'FlipY', 'Tilt', 'Saturation', 'Blend', 'Falloff', 'Opacity'].map(k => ['i' + k, { value: 0 }]))
  }
  sync = () => {
    for (const key of ['speed', 'intensity', 'spread', 'tilt', 'saturation', 'blend', 'falloff', 'opacity'])
      uniforms['i' + key[0].toUpperCase() + key.slice(1)].value = props[key]
    uniforms.iRayColor1.value = rgb(props.rayColor1)
    uniforms.iRayColor2.value = rgb(props.rayColor2)
    uniforms.iFlipX.value = props.origin.endsWith('left') ? 1 : 0
    uniforms.iFlipY.value = props.origin.startsWith('bottom') ? 1 : 0
  }
  sync()
  const geometry = new Triangle(gl)
  const program = new Program(gl, {
    vertex: `attribute vec2 position;
void main() { gl_Position = vec4(position, 0.0, 1.0); }`,
    fragment: `precision highp float;
uniform float iTime;
uniform vec2 iResolution;
uniform float iSpeed;
uniform vec3 iRayColor1;
uniform vec3 iRayColor2;
uniform float iIntensity;
uniform float iSpread;
uniform float iFlipX;
uniform float iFlipY;
uniform float iTilt;
uniform float iSaturation;
uniform float iBlend;
uniform float iFalloff;
uniform float iOpacity;
float rayStrength(vec2 raySource, vec2 rayRefDirection, vec2 coord, float seedA, float seedB, float speed) {
  vec2 sourceToCoord = coord - raySource;
  float cosAngle = dot(normalize(sourceToCoord), rayRefDirection);
  return clamp((0.45 + 0.15 * sin(cosAngle * seedA + iTime * speed)) +
    (0.3 + 0.2 * cos(-cosAngle * seedB + iTime * speed)), 0.0, 1.0) *
    clamp((iResolution.x - length(sourceToCoord)) / iResolution.x, 0.5, 1.0);
}
void main() {
  vec2 fragCoord = gl_FragCoord.xy;
  if (iFlipX > 0.5) fragCoord.x = iResolution.x - fragCoord.x;
  if (iFlipY > 0.5) fragCoord.y = iResolution.y - fragCoord.y;
  vec2 coord = vec2(fragCoord.x, iResolution.y - fragCoord.y);
  vec2 rayPos = vec2(iResolution.x * 1.1, -0.5 * iResolution.y);
  float tiltRad = iTilt * 3.14159265 / 180.0;
  float cs = cos(tiltRad);
  float sn = sin(tiltRad);
  vec2 rel = coord - rayPos;
  vec2 tiltedCoord = vec2(rel.x * cs - rel.y * sn, rel.x * sn + rel.y * cs) + rayPos;
  float halfSpread = iSpread * 0.275;
  vec2 rayRefDir1 = normalize(vec2(cos(0.785398 + halfSpread), sin(0.785398 + halfSpread)));
  vec2 rayRefDir2 = normalize(vec2(cos(0.785398 - halfSpread), sin(0.785398 - halfSpread)));
  vec4 rays1 = vec4(iRayColor1, 1.0) * rayStrength(rayPos, rayRefDir1, tiltedCoord, 36.2214, 21.11349, iSpeed);
  vec4 rays2 = vec4(iRayColor2, 1.0) * rayStrength(rayPos, rayRefDir2, tiltedCoord, 22.3991, 18.0234, iSpeed * 0.2);
  vec4 color = rays1 * (1.0 - iBlend) * 0.9 + rays2 * iBlend * 0.9;
  float distanceToLight = length(fragCoord.xy - vec2(rayPos.x, iResolution.y - rayPos.y)) / iResolution.y;
  float brightness = iIntensity * 0.4 / pow(max(distanceToLight, 0.001), iFalloff);
  color.rgb *= brightness;
  float gray = dot(color.rgb, vec3(0.299, 0.587, 0.114));
  color.rgb = mix(vec3(gray), color.rgb, iSaturation);
  color.a = max(color.r, max(color.g, color.b)) * iOpacity;
  gl_FragColor = color;
}`, uniforms
  })
  const mesh = new Mesh(gl, { geometry, program })
  el.appendChild(gl.canvas)
  let frame = 0, visible = false, lost = false, elapsed = 0, last = 0
  const motion = matchMedia('(prefers-reduced-motion: reduce)')
  const draw = t => {
    frame = 0
    if (cancelled || lost) return
    if (last) elapsed += Math.min(t - last, 64)
    last = t
    uniforms.iTime.value = motion.matches ? 0 : elapsed * 0.001
    renderer.render({ scene: mesh })
    if (visible && !document.hidden && !motion.matches) frame = requestAnimationFrame(draw)
  }
  const schedule = () => {
    cancelAnimationFrame(frame); frame = 0; last = 0
    if (visible && !document.hidden && !lost) frame = requestAnimationFrame(draw)
  }
  const resize = () => {
    if (!el.clientWidth || !el.clientHeight) return
    renderer.dpr = Math.min(devicePixelRatio, 2)
    renderer.setSize(el.clientWidth, el.clientHeight)
    uniforms.iResolution.value = [gl.drawingBufferWidth, gl.drawingBufferHeight]
    schedule()
  }
  const syncUniforms = sync
  sync = () => { syncUniforms(); schedule() }
  const observer = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; schedule() })
  const sizes = new ResizeObserver(resize)
  const onLost = () => { lost = true; cancelAnimationFrame(frame) }
  gl.canvas.addEventListener('webglcontextlost', onLost)
  observer.observe(el); sizes.observe(el)
  document.addEventListener('visibilitychange', schedule)
  motion.addEventListener('change', schedule)
  resize()
  dispose = () => {
    cancelAnimationFrame(frame)
    observer.disconnect(); sizes.disconnect()
    document.removeEventListener('visibilitychange', schedule)
    motion.removeEventListener('change', schedule)
    gl.canvas.removeEventListener('webglcontextlost', onLost)
    geometry.remove(); program.remove()
    gl.getExtension('WEBGL_lose_context')?.loseContext()
    gl.canvas.remove()
    sync = () => {}
  }
})
</script>

<template><div ref="container" class="side-rays-container" aria-hidden="true" /></template>
