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
  const activation = useMemo(() => mockActivation(scenario), [scenario]);
  return (
    <>
      <label>
        {zh ? '模拟库状态 ' : 'Simulated library state '}
        <Select
          value={[scenario]}
          onChange={([value]) => {
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
      <OpenVikingProvider fetch={transport}>
        <div style={{ height: 560 }}>
          <Console
            locale={zh ? 'zh-CN' : 'en-US'}
            scopeKey={scenario}
            chatOptions={{ activation, service }}
          />
        </div>
      </OpenVikingProvider>
    </>
  );
}
