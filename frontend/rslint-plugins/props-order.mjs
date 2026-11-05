/**
 * 公开 Props 的字段顺序规则，规范见 packages/react/AGENTS.md「Props 字段顺序」。
 *
 * 两条独立的规则共同决定顺序：
 * - 必选性：必选字段在前，可选字段在后。
 * - 字段类别：参数 → 回调 → 插槽 → 文案 → 低频配置。
 *
 * 先按必选性分组，组内再按类别排列。回调一律可选，因此「必选回调」本身即违规。
 */

/** 低频配置字段，永远排在最后。 */
const TAIL_FIELDS = ['slots', 'labels', 'requestOptions'];

/** 字段类别，数字越小越靠前。 */
const KIND = {
  parameter: 0,
  callback: 1,
  slots: 2,
  labels: 3,
  config: 4,
};

const KIND_NAME = {
  [KIND.parameter]: 'parameter',
  [KIND.callback]: 'callback',
  [KIND.slots]: 'slot',
  [KIND.labels]: 'label',
  [KIND.config]: 'config',
};

/** 判断一个字段的类别。 */
function kindOf(name) {
  if (name === 'slots') return KIND.slots;
  if (name === 'labels') return KIND.labels;
  if (TAIL_FIELDS.includes(name)) return KIND.config;
  // on* 是事件回调；render* / get* 返回节点或值，同属回调类别。
  if (/^(on[A-Z]|render[A-Z]|get[A-Z])/.test(name)) return KIND.callback;
  return KIND.parameter;
}

/** 判断是否是事件回调，用于「回调必须可选」的检查。 */
function isEventCallback(name) {
  return /^on[A-Z]/.test(name);
}

/** 按两条规则算出排序键。 */
function sortKeyOf(member) {
  const optional = member.optional === true;
  const kind = kindOf(member.key.name);
  return (optional ? 1 : 0) * 10 + kind;
}

const rule = {
  meta: {
    type: 'suggestion',
    docs: {
      description:
        'Enforce the public Props field order: required before optional, and parameters before callbacks, slots, labels and low-frequency configuration.',
    },
    schema: [],
    messages: {
      order:
        'Props field "{{name}}" ({{actual}}) must not follow "{{previous}}" ({{previousDesc}}). Order: required parameters, optional parameters, callbacks, slots, labels, requestOptions.',
      callbackRequired:
        'Props field "{{name}}" is an event callback and must be optional.',
    },
  },

  create(context) {
    return {
      TSInterfaceDeclaration(node) {
        // 只检查公开的 Props 接口。
        if (!/Props$/.test(node.id.name)) return;

        const members = node.body.body.filter(
          (member) =>
            member.type === 'TSPropertySignature' &&
            member.key.type === 'Identifier',
        );

        let previous = null;

        for (const member of members) {
          const name = member.key.name;

          if (isEventCallback(name) && member.optional !== true) {
            context.report({
              node: member,
              messageId: 'callbackRequired',
              data: { name },
            });
          }

          const key = sortKeyOf(member);
          if (previous !== null && key < previous.key) {
            context.report({
              node: member,
              messageId: 'order',
              data: {
                name,
                actual: describe(member),
                previous: previous.name,
                previousDesc: describe(previous.member),
              },
            });
          }
          previous = { key, name, member };
        }
      },
    };
  },
};

function describe(member) {
  const optional = member.optional === true ? 'optional' : 'required';
  return `${optional} ${KIND_NAME[kindOf(member.key.name)]}`;
}

export default {
  meta: { name: 'props-order' },
  rules: { 'props-order': rule },
};
