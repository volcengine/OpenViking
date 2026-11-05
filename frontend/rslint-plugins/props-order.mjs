/**
 * 公开 Props 的字段顺序规则，规范见 packages/react/AGENTS.md「Props 字段顺序」。
 *
 * 两条独立的规则共同决定顺序：
 * - 必选性：必选字段在前，可选字段在后。
 * - 字段类别：参数 → 回调 → 插槽 → 文案 → 低频配置。
 *
 * 先按必选性分组，组内再按类别排列。回调一律可选，因此「必选回调」本身即违规。
 *
 * 回调按类型判定：字段类型是函数类型（`(x) => void` 或 `new () => T`）即为回调。
 * 类型别名、泛型与访问器暂不解析。
 */

/** 按名称判定的字段类别。 */
const NAMED_KINDS = {
  slots: 'slots',
  labels: 'labels',
  requestOptions: 'config',
};

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

/** 字段类型是函数或构造函数类型即为回调。 */
function isCallbackMember(member) {
  const type = member.typeAnnotation?.typeAnnotation;
  return type?.type === 'TSFunctionType' || type?.type === 'TSConstructorType';
}

/** 判断一个字段的类别。 */
function kindOf(member) {
  const named = NAMED_KINDS[member.key.name];
  if (named) return KIND[named];
  if (isCallbackMember(member)) return KIND.callback;
  return KIND.parameter;
}

/** 按两条规则算出排序键。 */
function sortKeyOf(member) {
  const optional = member.optional === true;
  return (optional ? 1 : 0) * 10 + kindOf(member);
}

function describe(member) {
  const optional = member.optional === true ? 'optional' : 'required';
  return `${optional} ${KIND_NAME[kindOf(member)]}`;
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
        'Props field "{{name}}" is a callback and must be optional.',
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

          if (isCallbackMember(member) && member.optional !== true) {
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

export default {
  meta: { name: 'props-order' },
  rules: { 'props-order': rule },
};
