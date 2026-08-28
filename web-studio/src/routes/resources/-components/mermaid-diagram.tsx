import { mermaid } from '@streamdown/mermaid'
import { Streamdown } from 'streamdown'

const plugins = { mermaid }

export function MermaidDiagram({ chart }: { chart: string }) {
  return (
    <Streamdown mode="static" plugins={plugins} lineNumbers={false}>
      {`\`\`\`mermaid\n${chart}\n\`\`\``}
    </Streamdown>
  )
}
