import type { BotChatImage } from '@ov-server/bot/v1/chat'

import { createBrowserId } from '../browser-crypto'

/** Mirrors the bot chat endpoint limits (`bot/vikingbot/channels/openapi_models.py`). */
export const MAX_SESSION_IMAGES = 4
export const MAX_SESSION_IMAGE_BYTES = 10 * 1024 * 1024

const SUPPORTED_IMAGE_TYPES = new Set([
  'image/gif',
  'image/jpeg',
  'image/png',
  'image/webp',
])

/** Longest edge kept after client-side downscaling, in CSS pixels. */
const MAX_IMAGE_EDGE = 1600
const JPEG_QUALITY = 0.85
/** Small originals are kept byte-for-byte instead of being re-encoded. */
const KEEP_ORIGINAL_BYTES = 1.5 * 1024 * 1024

export type DraftImage = {
  id: string
  name: string
  /** `data:image/...;base64,...` payload understood by the chat endpoint. */
  dataUrl: string
}

export type ImagePrepareFailure = 'unsupported' | 'tooLarge' | 'decodeFailed'

export class ImagePrepareError extends Error {
  readonly reason: ImagePrepareFailure

  constructor(reason: ImagePrepareFailure) {
    super(reason)
    this.name = 'ImagePrepareError'
    this.reason = reason
  }
}

export function isSupportedImageType(type: string): boolean {
  return SUPPORTED_IMAGE_TYPES.has(type)
}

export function draftImageToPart(image: DraftImage): BotChatImage {
  return { type: 'image_url', image_url: { url: image.dataUrl } }
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result
      if (typeof result === 'string' && result.startsWith('data:')) resolve(result)
      else reject(new ImagePrepareError('decodeFailed'))
    }
    reader.onerror = () => reject(new ImagePrepareError('decodeFailed'))
    reader.readAsDataURL(file)
  })
}

function decodeImage(file: File): Promise<HTMLImageElement> {
  const url = URL.createObjectURL(file)
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      URL.revokeObjectURL(url)
      resolve(img)
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new ImagePrepareError('decodeFailed'))
    }
    img.src = url
  })
}

async function downscale(file: File): Promise<string> {
  const img = await decodeImage(file)
  const longestEdge = Math.max(img.naturalWidth, img.naturalHeight)
  const scale = longestEdge > MAX_IMAGE_EDGE ? MAX_IMAGE_EDGE / longestEdge : 1
  const width = Math.max(1, Math.round(img.naturalWidth * scale))
  const height = Math.max(1, Math.round(img.naturalHeight * scale))

  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new ImagePrepareError('decodeFailed')
  ctx.drawImage(img, 0, 0, width, height)

  // PNG may carry transparency, so keep PNG for it; other types go to JPEG.
  const outputType = file.type === 'image/png' ? 'image/png' : 'image/jpeg'
  return canvas.toDataURL(outputType, JPEG_QUALITY)
}

/**
 * Turn a picked/pasted/dropped file into a chat-ready inline image.
 * Throws {@link ImagePrepareError} when the file cannot be attached.
 */
export async function prepareImageFile(file: File): Promise<DraftImage> {
  if (!isSupportedImageType(file.type)) throw new ImagePrepareError('unsupported')
  if (file.size > MAX_SESSION_IMAGE_BYTES) throw new ImagePrepareError('tooLarge')

  // Animated GIFs lose their animation on canvas, so keep the original bytes.
  let dataUrl: string
  if (file.type === 'image/gif' || file.size <= KEEP_ORIGINAL_BYTES) {
    dataUrl = await readAsDataUrl(file)
  } else {
    dataUrl = await downscale(file)
  }
  if (dataUrl.length > MAX_SESSION_IMAGE_BYTES) throw new ImagePrepareError('tooLarge')

  return { id: createBrowserId('img'), name: file.name, dataUrl }
}

export async function prepareImageFiles(
  files: File[],
  remainingSlots: number,
): Promise<{ images: DraftImage[]; errors: ImagePrepareFailure[] }> {
  const images: DraftImage[] = []
  const errors: ImagePrepareFailure[] = []
  for (const file of files.slice(0, Math.max(0, remainingSlots))) {
    try {
      images.push(await prepareImageFile(file))
    } catch (error) {
      errors.push(
        error instanceof ImagePrepareError ? error.reason : 'decodeFailed',
      )
    }
  }
  return { images, errors }
}
