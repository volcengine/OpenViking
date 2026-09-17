import { useState } from 'react'
import {
  ChevronDownIcon,
  ChevronUpIcon,
  CopyIcon,
  ExternalLinkIcon,
  SparklesIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverTitle,
  PopoverTrigger,
} from '#/components/ui/popover'
import { copyTextToClipboard } from '#/lib/clipboard'

import { EvolutionSettingsPopover } from './evolution-settings-popover'

const INSTALL_COMMAND =
  'npx skills add https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin/skills/ov-experience-memory'

export function ExperienceSetupGuide() {
  const [expanded, setExpanded] = useState(false)
  const { t, i18n } = useTranslation('agentExperiencePage')
  const docsLanguage = i18n.language.startsWith('zh') ? 'zh' : 'en'

  async function copyCommand() {
    try {
      await copyTextToClipboard(INSTALL_COMMAND)
      toast.success(t('setup.copied'))
    } catch {
      toast.error(t('setup.copyFailed'))
    }
  }

  return (
    <section
      aria-labelledby="experience-setup-title"
      className="rounded-lg border border-border/60 bg-muted/10 px-3 py-1.5"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <SparklesIcon className="size-4 shrink-0 text-muted-foreground" />
          <h2 id="experience-setup-title" className="text-sm font-medium">
            {t('setup.title')}
          </h2>
        </div>
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={expanded}
          aria-controls="experience-setup-steps"
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? t('setup.collapse') : t('setup.expand')}
          {expanded ? <ChevronUpIcon /> : <ChevronDownIcon />}
        </Button>
      </div>
      <div id="experience-setup-steps" hidden={!expanded}>
        <ol className="mt-4 divide-y divide-border/60 text-sm">
          <li className="flex flex-wrap items-center gap-3 py-3">
            <StepNumber number={1} />
            <span>{t('setup.connect')}</span>
            <Button
              render={
                <a
                  href={`https://docs.openviking.ai/${docsLanguage}/agent-integrations/01-overview`}
                  target="_blank"
                  rel="noopener noreferrer"
                />
              }
              nativeButton={false}
              variant="outline"
              size="sm"
            >
              {t('setup.docs')}
              <ExternalLinkIcon />
            </Button>
          </li>
          <li className="py-3">
            <div className="flex flex-wrap items-center gap-3">
              <StepNumber number={2} />
              <span>{t('setup.install')}</span>
              <Popover>
                <PopoverTrigger
                  render={
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-primary"
                    />
                  }
                >
                  {t('setup.view')}
                </PopoverTrigger>
                <PopoverContent
                  className="w-[min(28rem,calc(100vw-2rem))]"
                  sideOffset={8}
                >
                  <PopoverTitle>{t('setup.install')}</PopoverTitle>
                  <PopoverDescription>{t('setup.hint')}</PopoverDescription>
                  <pre className="rounded-lg border bg-muted/50 p-4 text-xs leading-6 whitespace-pre-wrap break-all select-text">
                    <code>{INSTALL_COMMAND}</code>
                  </pre>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-fit text-primary"
                    onClick={() => void copyCommand()}
                  >
                    <CopyIcon />
                    {t('setup.copy')}
                  </Button>
                </PopoverContent>
              </Popover>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void copyCommand()}
              >
                <CopyIcon />
                {t('setup.copy')}
              </Button>
            </div>
          </li>
          <li className="py-3">
            <div className="flex flex-wrap items-center gap-3">
              <StepNumber number={3} />
              <span>{t('setup.enable')}</span>
              <EvolutionSettingsPopover />
            </div>
            <p className="mt-2 text-muted-foreground sm:ml-9">
              {t('setup.enableHint')}
            </p>
          </li>
        </ol>
      </div>
    </section>
  )
}

function StepNumber({ number }: { number: number }) {
  return (
    <span
      aria-hidden="true"
      className="flex size-6 shrink-0 items-center justify-center rounded-full border border-dashed border-primary/50 text-xs font-medium text-primary"
    >
      {number}
    </span>
  )
}
