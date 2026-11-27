// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';

// Lark renders a voice message (a playable bubble with a transcript) only for
// Ogg Opus: lark-cli uploads `--audio` as a voice message for .opus and .ogg,
// and takes every other audio container as an ordinary file. The extension
// alone does not make a file Ogg Opus though (a .opus can hold a bare Opus
// stream, a .ogg Vorbis), and Lark takes the length it shows from the Ogg page
// granule of the uploaded file, so a file that is not Ogg Opus uploads without
// one and renders as a 00:01 bubble. These two are therefore checked before the
// send, the containers below are converted to Opus first, and everything else
// (.m4v, .pdf, ...) stays a plain attachment.
const VOICE_TYPES = new Set(['.opus', '.ogg']);
const CONVERTIBLE_AUDIO_TYPES = new Set([
  '.mp3', '.wav', '.m4a', '.aac', '.flac', '.amr', '.wma', '.aiff', '.aif', '.caf', '.oga',
]);

// One ffmpeg conversion. The Core adds it to the connector call budget of a
// voice send, ahead of the lark-cli send itself.
export const LARK_AUDIO_CONVERT_TIMEOUT_MS = 30_000;
export const LARK_AUDIO_PREP_MS = LARK_AUDIO_CONVERT_TIMEOUT_MS + 1_000;

const extension = (file) => path.extname(file).toLowerCase();

export function isLarkAudio(file) {
  const ext = extension(file);
  return VOICE_TYPES.has(ext) || CONVERTIBLE_AUDIO_TYPES.has(ext);
}

export function needsOpusConversion(file) {
  return CONVERTIBLE_AUDIO_TYPES.has(extension(file));
}

const OGG_OPUS_SAMPLE_RATE = 48_000;
// lark-cli reads only the last 64 KiB of the file, looking for its last page.
const OGG_OPUS_TAIL_BYTES = 64 * 1024;

function readRange(fd, position, length) {
  const buffer = Buffer.allocUnsafe(length);
  const read = fs.readSync(fd, buffer, 0, length, position);
  return buffer.subarray(0, read);
}

// The length in milliseconds Lark will show for `file`, read from the granule
// position of its last Ogg page the way lark-cli reads it before uploading a
// voice message; null when the file is not Ogg Opus, which is exactly when
// lark-cli uploads no duration and Lark renders 00:01 instead.
export function probeVoiceDurationMs(file) {
  let fd;
  try {
    fd = fs.openSync(file, 'r');
    const size = fs.fstatSync(fd).size;
    const head = readRange(fd, 0, Math.min(size, 4_096));
    if (head.length < 4 || head.toString('latin1', 0, 4) !== 'OggS' || !head.includes('OpusHead')) return null;
    const tail = readRange(fd, Math.max(0, size - OGG_OPUS_TAIL_BYTES), Math.min(size, OGG_OPUS_TAIL_BYTES));
    const page = tail.lastIndexOf('OggS');
    if (page < 0 || page + 14 > tail.length) return null;
    const granule = tail.readBigUInt64LE(page + 6);
    return granule > 0n ? Math.round(Number(granule) * 1000 / OGG_OPUS_SAMPLE_RATE) : null;
  } catch {
    return null;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

// Whether a file isLarkAudio accepts has to be re-encoded before the send:
// another container, or a .opus/.ogg that is not Ogg Opus and would show up as
// a one-second bubble.
export function needsVoiceConversion(file) {
  return needsOpusConversion(file) || probeVoiceDurationMs(file) === null;
}

export function convertHint(file) {
  const name = path.basename(file);
  const target = `${name.slice(0, name.length - extension(name).length) || 'voice'}.opus`;
  return `convert it with "ffmpeg -i ${name} -vn -ac 1 -ar 48000 -c:a libopus ${target}" and send that`;
}

// Writes `input` as Ogg Opus into `directory` and returns its path. Lark's
// voice message is Ogg Opus only, so an mp3/wav/m4a, and a .opus/.ogg that is
// not Ogg Opus, has to be re-encoded before the send; ffmpeg is the encoder
// every lark-cli deployment already has the option of using for video covers.
export async function toOpus(registry, input, directory, logger) {
  const output = path.join(directory, 'voice.opus');
  const result = await registry.run('ffmpeg', [
    '-y', '-i', input, '-vn', '-ac', '1', '-ar', '48000', '-c:a', 'libopus', '-b:a', '32k', output,
  ], { timeoutMs: LARK_AUDIO_CONVERT_TIMEOUT_MS }).catch((error) => {
    throw new Error(`Lark takes voice messages only as Ogg Opus and ${path.basename(input)} needs converting, `
      + `but ffmpeg could not run (${error.message}); ${convertHint(input)}, or install ffmpeg`);
  });
  if (result.timedOut) {
    throw new Error(`converting ${path.basename(input)} to Opus timed out after ${LARK_AUDIO_CONVERT_TIMEOUT_MS}ms; `
      + `${convertHint(input)}`);
  }
  if (result.code !== 0 || !fs.existsSync(output) || !fs.statSync(output).size) {
    const reason = result.stderr?.trim().split('\n').at(-1)?.slice(-300);
    throw new Error(`ffmpeg could not convert ${path.basename(input)} to Opus`
      + `${reason ? `: ${reason}` : ''}; ${convertHint(input)}`);
  }
  logger.debug('lark_voice_converted', { file: path.basename(input), bytes: fs.statSync(output).size });
  return output;
}
