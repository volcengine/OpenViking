import type { Context } from '@deepseek-ai/cordis'
import { LlmAdapter, type StreamChunk } from '@deepseek-ai/dsh-llm'

/** Deterministic one-step adapter for the plugin Loader fixture. */
class OvPluginMockAdapter extends LlmAdapter {
  async * stream(): AsyncIterable<StreamChunk> {
    const text = 'ov plugin mock reply'
    yield { type: 'block-start', index: 0, blockType: 'text' }
    yield { type: 'text-delta', index: 0, text }
    yield { type: 'block-end', index: 0, block: { type: 'text', text } }
    yield { type: 'usage', usage: { inputTokens: 1, outputTokens: 1 } }
    yield { type: 'finish', reason: { kind: 'stop' } }
  }
}

export const name = 'ov-plugin-mock-llm'
export const inject = ['llm']

/** Register the test-only `ov-plugin-mock` adapter. */
export function apply(ctx: Context): void {
  ctx.llm.registerAdapter(['ov-plugin-mock'], new OvPluginMockAdapter())
}
