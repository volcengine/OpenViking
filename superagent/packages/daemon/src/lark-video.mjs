// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';

// lark-cli uploads .mp4, .mkv, .webm and .avi as Lark video files, so a plain
// --file send of them is rejected (230055). .mp4 and .mov go out as a playable
// media message; .mkv, .webm and .avi cannot be sent at all and must be
// converted first. Other extensions (.m4v, say) still upload as plain files.
const VIDEO_TYPES = new Set(['.mp4', '.mov']);
const UNSENDABLE_VIDEO_TYPES = new Set(['.mkv', '.webm', '.avi']);

// Each ffprobe/ffmpeg step of cover extraction. The Core adds both to the
// connector call budget of a video send, ahead of the lark-cli send itself.
export const LARK_VIDEO_STEP_TIMEOUT_MS = 15_000;
export const LARK_VIDEO_PREP_MS = 2 * LARK_VIDEO_STEP_TIMEOUT_MS;

// lark-cli requires a cover with every video. This 64x36 dark PNG stands in
// when ffmpeg is missing or cannot read a frame, so sending a video never
// depends on ffmpeg.
const PLACEHOLDER_COVER = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAEAAAAAkCAIAAAC2bqvFAAAAOklEQVR42u3PQQkAAAgEsEsj+'
  + 'LF/PDP4FAYrsFTPaxEQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQuFnrz2U9wDRD6wAAAABJRU5ErkJggg==', 'base64');

const extension = (file) => path.extname(file).toLowerCase();

export function isLarkVideo(file) {
  return VIDEO_TYPES.has(extension(file));
}

// The error for a video container Lark takes neither as video nor as a file.
export function unsendableVideoError(file) {
  if (!UNSENDABLE_VIDEO_TYPES.has(extension(file))) return null;
  const name = path.basename(file);
  return new Error(`Lark cannot send ${name}: it takes videos only as .mp4 or .mov and rejects ${extension(file)} `
    + 'even as a file. Convert it to an H.264/AAC .mp4 and send that, or upload it to Drive and send the link.');
}

// Writes a cover for `video` into `directory` and returns its path: a frame
// from min(1s, half the duration), or the placeholder when that fails.
export async function videoCover(registry, video, directory, logger) {
  const cover = path.join(directory, 'cover.jpg');
  try {
    await extractFrame(registry, video, cover);
    return cover;
  } catch (error) {
    logger.warn('lark_video_cover_placeholder', { file: path.basename(video), error: error.message });
    const placeholder = path.join(directory, 'cover.png');
    fs.writeFileSync(placeholder, PLACEHOLDER_COVER, { mode: 0o600 });
    return placeholder;
  }
}

async function extractFrame(registry, input, output) {
  const timeoutMs = LARK_VIDEO_STEP_TIMEOUT_MS;
  let seek = 1;
  const probe = await registry.run('ffprobe', [
    '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', input,
  ], { timeoutMs }).catch(() => null);
  const duration = Number.parseFloat(probe?.code === 0 ? probe.stdout : '');
  if (Number.isFinite(duration) && duration > 0) seek = Math.min(1, duration / 2);
  const result = await registry.run('ffmpeg', [
    '-y', '-ss', String(seek), '-i', input, '-frames:v', '1', '-q:v', '3', output,
  ], { timeoutMs });
  if (result.timedOut) throw new Error('ffmpeg timed out');
  if (result.code !== 0) {
    const reason = result.stderr.trim().split('\n').at(-1)?.slice(-300);
    throw new Error(`ffmpeg exit ${result.code}${reason ? `: ${reason}` : ''}`);
  }
  if (!fs.existsSync(output) || !fs.statSync(output).size) throw new Error('ffmpeg produced no cover');
}
