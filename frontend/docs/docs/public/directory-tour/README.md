# Directory tour illustrations

Source: user-supplied OpenViking Console design in Figma, file
`ecpd1ZGHKRYibm1ODKEpM6`. These are the original downloaded raster assets,
used as host-supplied illustration slots in the component documentation.
No temporary asset URL is used at runtime.

| File       | Content node | Target             |
| ---------- | ------------ | ------------------ |
| step-1.png | 10809:106850 | User memories      |
| step-2.png | 10809:106871 | Sessions           |
| step-3.png | 10809:106892 | Personal resources |
| step-4.png | 10809:106913 | Shared resources   |
| step-5.png | 10809:125753 | Experiences        |
| step-6.png | 10809:125774 | Peers              |

The library itself has no image URL dependency; applications supply their own
`illustration` React nodes and asset hosting, as demonstrated here.

Team introduction assets:

- `team/cover.png`: original media composition exported from Figma `10168:334213` (246 × 159). Includes the designed play affordance; rendered inside a host-owned button.
- `team/folder-open.svg`: `10168:334234` (11.2 × 11.2).
- `team/arrow-up-right.svg`: `10168:334540` (12 × 12).
  Source: OpenViking Console, node `10168:334211`. These local demo assets do not introduce runtime Figma or private CDN dependencies.
