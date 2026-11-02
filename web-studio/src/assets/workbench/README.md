# Workbench auth illustration

`team.png` is reused from the user's existing
`vikingdb-fe/apps/ov-workbench/src/assets/auth/team.png` for the Studio UX
migration on 2026-11-02. The image pixels are unchanged; the PNG includes origin
metadata.

The adjacent layout and palette are adapted in
`features/auth/components/auth-layout.module.css` and
`shared/styles/workbench-theme.css`. Studio retains its own UI components;
the source project's Arco dependencies and mock login behavior are not copied.

## Figma alignment

The login reference is node `9164:206564` within section `9354:32612` in
[OpenViking Console](https://www.figma.com/design/ecpd1ZGHKRYibm1ODKEpM6/OpenViking-Console?node-id=9164-206564).
`team.png` has the same dimensions and decoded PNG pixel stream as the Figma
source image. `logo-mark.svg` (32×32) and `eye.svg` (16×16) are unchanged exports
from that node. They are local source assets, not temporary Figma URLs.

Workbench UX routes use the design's light palette even when native Studio uses
a saved dark/system theme. Native Studio routes retain their theme preference.
