import MarkdownIt from 'markdown-it'
import { FilterXSS } from 'xss'

const markdown = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  typographer: true
})

const defaultLinkOpen = markdown.renderer.rules.link_open
markdown.renderer.rules.link_open = (tokens, index, options, env, self) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen
    ? defaultLinkOpen(tokens, index, options, env, self)
    : self.renderToken(tokens, index, options)
}

const sanitizer = new FilterXSS({
  whiteList: {
    a: ['href', 'title', 'target', 'rel'],
    blockquote: [],
    br: [],
    code: ['class'],
    em: [],
    h1: [],
    h2: [],
    h3: [],
    h4: [],
    li: [],
    ol: [],
    p: [],
    pre: [],
    strong: [],
    table: [],
    tbody: [],
    td: [],
    th: [],
    thead: [],
    tr: [],
    ul: []
  },
  stripIgnoreTag: true,
  stripIgnoreTagBody: ['script', 'style']
})

export function renderVikingBotMarkdown(content: string) {
  return sanitizer.process(markdown.render(content))
}
