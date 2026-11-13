// Checks semantic message boundaries outside JSX. Arbitrary protocol strings,
// user content and server diagnostics are deliberately not translated here.
function literalText(node) {
  if (!node) return false
  if (node.type === 'Literal')
    return typeof node.value === 'string' && /\p{L}/u.test(node.value)
  if (node.type === 'TemplateLiteral')
    return node.quasis.some((part) =>
      /\p{L}/u.test(part.value.cooked ?? part.value.raw),
    )
  if (node.type === 'ConditionalExpression')
    return literalText(node.consequent) || literalText(node.alternate)
  if (node.type === 'LogicalExpression' || node.type === 'BinaryExpression')
    return literalText(node.left) || literalText(node.right)
  return false
}

export const noLiteralMessage = {
  meta: {
    type: 'problem',
    schema: [],
    messages: {
      translate:
        'Move user-visible messages to both language resources and use t(). For internal diagnostics, disable this rule on the line with a reason.',
    },
  },
  create(context) {
    function check(node, reportNode = node) {
      const seen = new Set()
      function containsText(value) {
        if (!value || seen.has(value)) return false
        seen.add(value)
        if (value.type === 'Identifier') {
          for (
            let scope = context.sourceCode.getScope(value);
            scope;
            scope = scope.upper
          ) {
            const variable = scope.set.get(value.name)
            if (!variable) continue
            const definition = variable.defs[0]
            return (
              definition?.parent?.kind === 'const' &&
              containsText(definition.node.init)
            )
          }
        }
        if (value.type === 'ConditionalExpression')
          return containsText(value.consequent) || containsText(value.alternate)
        if (
          value.type === 'LogicalExpression' ||
          value.type === 'BinaryExpression'
        )
          return containsText(value.left) || containsText(value.right)
        return literalText(value)
      }
      if (containsText(node))
        context.report({ node: reportNode, messageId: 'translate' })
    }
    return {
      JSXExpressionContainer(node) {
        const parent = node.parent
        if (
          parent.type === 'JSXAttribute' &&
          !/^(title|placeholder|label|tooltip|description|alt|aria-label|aria-description|ariaLabel|helperText|emptyText)$/.test(
            parent.name.name,
          )
        )
          return
        // The upstream JSX rule handles literals. Also check template fragments
        // without mistaking interpolated identifiers or translation keys for text.
        function template(expr) {
          if (expr.type === 'TemplateLiteral') check(expr)
          else if (expr.type === 'ConditionalExpression') {
            template(expr.consequent)
            template(expr.alternate)
          } else if (expr.type === 'LogicalExpression') {
            template(expr.left)
            template(expr.right)
          }
        }
        template(node.expression)
      },
      NewExpression(node) {
        if (
          node.callee.type === 'Identifier' &&
          ['Error', 'DOMException', 'OvClientError'].includes(node.callee.name)
        ) {
          // AbortError is a control-flow signal, never a user-facing failure.
          if (
            node.callee.name === 'DOMException' &&
            node.arguments[1]?.value === 'AbortError'
          )
            return
          const arg = node.arguments[0]
          if (arg?.type === 'ObjectExpression') {
            for (const prop of arg.properties) {
              if (
                prop.type === 'Property' &&
                (prop.key.name ?? prop.key.value) === 'message'
              )
                check(prop.value, node)
            }
          } else check(arg, node)
        }
      },
      CallExpression(node) {
        const callee = node.callee
        if (
          callee.type === 'Identifier' &&
          /^set\w*(Error|Message)$/.test(callee.name)
        )
          check(node.arguments[0])
        if (
          (callee.type === 'Identifier' && callee.name === 'toast') ||
          (callee.type === 'MemberExpression' &&
            callee.object.type === 'Identifier' &&
            callee.object.name === 'toast' &&
            (callee.computed ? callee.property.value : callee.property.name) !==
              'dismiss')
        ) {
          check(node.arguments[0])
          const options = node.arguments[1]
          if (options?.type === 'ObjectExpression') {
            for (const prop of options.properties) {
              if (
                prop.type === 'Property' &&
                (prop.key.name ?? prop.key.value) === 'description'
              )
                check(prop.value)
            }
          }
        }
      },
    }
  },
}

export const explicitLocale = {
  meta: {
    type: 'problem',
    schema: [],
    messages: {
      locale:
        'Display dates and numbers with the Studio language (or use #/i18n/format), not the browser default.',
    },
  },
  create(context) {
    return {
      CallExpression(node) {
        const callee = node.callee
        const dateMethod =
          callee.type === 'MemberExpression' &&
          !callee.computed &&
          /^toLocale(String|DateString|TimeString)$/.test(callee.property.name)
        const intlFormat =
          callee.type === 'MemberExpression' &&
          callee.object.name === 'Intl' &&
          /^(DateTimeFormat|NumberFormat|RelativeTimeFormat|ListFormat)$/.test(
            callee.property.name,
          )
        if (!dateMethod && !intlFormat) return
        const locale = node.arguments[0]
        if (
          !locale ||
          (locale.type === 'Identifier' && locale.name === 'undefined') ||
          (locale.type === 'ArrayExpression' && !locale.elements.length)
        ) {
          context.report({ node, messageId: 'locale' })
        }
      },
      NewExpression(node) {
        if (
          node.callee.type !== 'MemberExpression' ||
          node.callee.object.name !== 'Intl' ||
          !/^(DateTimeFormat|NumberFormat|RelativeTimeFormat|ListFormat)$/.test(
            node.callee.property.name,
          )
        )
          return
        const locale = node.arguments[0]
        if (
          !locale ||
          (locale.type === 'Identifier' && locale.name === 'undefined') ||
          (locale.type === 'ArrayExpression' && !locale.elements.length)
        )
          context.report({ node, messageId: 'locale' })
      },
    }
  },
}

export default {
  rules: {
    'no-literal-message': noLiteralMessage,
    'explicit-locale': explicitLocale,
  },
}
