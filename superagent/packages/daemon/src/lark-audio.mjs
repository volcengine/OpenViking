// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';

// Lark renders a voice message (a playable bubble with a transcript) only for
// Opus: lark-cli uploads `--audio` as a voice message for .opus and Ogg Opus
// .ogg, and takes every other audio container as an ordinary file. So these two
// go straight to the bubble, the containers below are converted to Opus first,
// and everything else (.m4v, .pdf, ...) stays a plain attachment.
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

function convertHint(file) {
  const name = path.basename(file);
  const target = `${name.slice(0, name.length - extension(name).length) || 'voice'}.opus`;
  return `convert it with "ffmpeg -i ${name} -vn -ac 1 -ar 48000 -c:a libopus ${target}" and send that`;
}

// Writes `input` as Ogg Opus into `directory` and returns its path. Lark's
// voice message is Opus only, so an mp3/wav/m4a has to be re-encoded before
// the send; ffmpeg is the encoder every lark-cli deployment already has the
// option of using for video covers.
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
