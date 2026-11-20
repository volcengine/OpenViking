import { Select, SelectOption } from '#demo-controls';
import { botDemoService } from '#bot-demo-service';
import { consoleTransport } from '#console-transport';
import { OpenVikingProvider } from '@openviking/react/client';
import { Console } from '@openviking/react/modules/console';
import type { ConsoleBotActivation } from '@openviking/react/modules/console';
import '@openviking/react/modules/console/style.css';
import { useLang } from '@rspress/core/runtime';
import { useMemo, useState } from 'react';

type Scenario = 'enabled' | 'disabled' | 'start-failure';
type Intro = 'default' | 'text' | 'custom' | 'hidden';

const service = botDemoService('commercial');
const transport = consoleTransport();

function simulate(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    signal.throwIfAborted();
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort);
      resolve();
    }, 450);
    signal.addEventListener('abort', abort, { once: true });
  });
}

function mockActivation(scenario: Scenario): ConsoleBotActivation {
  let running = false;
  let failOnce = scenario === 'start-failure';
  async function getOpenVikingCollection(signal: AbortSignal) {
    await simulate(signal);
    return { EnableVikingBot: scenario !== 'disabled' };
  }
  async function getVikingBotStatus(signal: AbortSignal) {
    await simulate(signal);
    return { Status: running ? 'running' : 'not_started' };
  }
  return {
    async isEligible(signal) {
      const collection = await getOpenVikingCollection(signal);
      return collection.EnableVikingBot === true;
    },
    async getStatus(signal) {
      return (await getVikingBotStatus(signal)).Status === 'running'
        ? 'ready'
        : 'not-started';
    },
    async start(signal) {
      await simulate(signal);
      if (failOnce) {
        failOnce = false;
        throw new Error('The simulated start request failed.');
      }
      running = true;
    },
  };
}

export default function ConsoleActivation() {
  const zh = useLang() === 'zh';
  const [scenario, setScenario] = useState<Scenario>('enabled');
  const [intro, setIntro] = useState<Intro>('default');
  const activation = useMemo(() => mockActivation(scenario), [scenario]);
  return (
    <>
      <label>
        {zh ? '模拟库状态 ' : 'Simulated library state '}
        <Select<Scenario>
          value={[scenario]}
          onChange={([value]: Scenario[]) => {
            if (value) setScenario(value);
          }}
        >
          <SelectOption value="enabled">
            {zh ? '允许启用' : 'Eligible'}
          </SelectOption>
          <SelectOption value="disabled">
            {zh ? '库未开启 Bot' : 'Bot disabled'}
          </SelectOption>
          <SelectOption value="start-failure">
            {zh ? '首次启用失败' : 'First start fails'}
          </SelectOption>
        </Select>
      </label>
      <label>
        {zh ? '介绍区域 ' : 'Introduction '}
        <Select<Intro>
          value={[intro]}
          onChange={([value]: Intro[]) => {
            if (value) setIntro(value);
          }}
        >
          <SelectOption value="default">{zh ? '默认' : 'Default'}</SelectOption>
          <SelectOption value="text">
            {zh ? '覆盖文案' : 'Custom text'}
          </SelectOption>
          <SelectOption value="custom">
            {zh ? '替换整块' : 'Custom content'}
          </SelectOption>
          <SelectOption value="hidden">
            {zh ? '隐藏介绍' : 'Hide introduction'}
          </SelectOption>
        </Select>
      </label>
      <OpenVikingProvider fetch={transport}>
        <div style={{ height: 560 }}>
          <Console
            locale={zh ? 'zh-CN' : 'en-US'}
            scopeKey={scenario}
            chatOptions={{ activation, service }}
            labels={
              intro === 'text'
                ? {
                    activationTitle: zh
                      ? '为你的团队启用知识助手'
                      : 'Enable a knowledge assistant for your team',
                    activationDescription: zh
                      ? '由宿主提供介绍文案，保留内置的媒体和启用操作。'
                      : 'Host-provided copy with the built-in media and activation action.',
                  }
                : undefined
            }
            slots={{
              activationIntro:
                intro === 'hidden' ? null : intro === 'custom' ? (
                  <div style={{ textAlign: 'center' }}>
                    <h2>
                      {zh ? '你的专属知识空间' : 'Your own knowledge workspace'}
                    </h2>
                    <p>
                      {zh
                        ? '这整块介绍由宿主渲染，下面的启用操作仍由 Console 管理。'
                        : 'The host renders this introduction; Console still manages the activation action below.'}
                    </p>
                  </div>
                ) : undefined,
            }}
          />
        </div>
      </OpenVikingProvider>
    </>
  );
}
