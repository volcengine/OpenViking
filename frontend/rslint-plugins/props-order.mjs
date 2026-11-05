/**
 * 公开 Props 的字段顺序规则，规范见 packages/react/AGENTS.md「Props 字段顺序」。
 *
 * 两条独立的规则共同决定顺序：
 * - 必选性：必选字段在前，可选字段在后。
 * - 字段类别：参数 → 回调 → 插槽 → 文案 → 低频配置。
 *
 * 先按必选性分组，组内再按类别排列。回调一律可选，因此「必选回调」本身即违规。
 *
 * 回调按类型判定，与命名和书写形式无关：
 * - 函数类型 `(x) => void`、构造函数类型 `new () => T`；
 * - 指向上述类型的同文件类型别名，允许别名套别名；
 * - 方法签名 `renderItem(): void` 与交叉类型里的函数成员。
 * 因此 `onXxx`、`renderXxx`、`getXxx` 都是回调，而 `disableXxx` 这类布尔开关是
 * 参数。
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

/** 从接口成员或方法签名取出类型节点。 */
function typeNodeOf(member) {
  return member.typeAnnotation?.typeAnnotation;
}

/**
 * 判断一个类型节点是否为可调用类型。`aliases` 是本文件的类型别名表，用于把
 * `type Render = () => void` 这样的引用解析回函数类型。
 */
function isCallableType(type, aliases, seen = new Set()) {
  if (!type) return false;

  switch (type.type) {
    case 'TSFunctionType':
    case 'TSConstructorType':
      return true;

    case 'TSTypeReference': {
      // 只解析同文件的本地别名；外部类型无法在语法层面判断。
      if (type.typeName.type !== 'Identifier') return false;
      const name = type.typeName.name;
      if (seen.has(name)) return false;
      const target = aliases.get(name);
      if (!target) return false;
      seen.add(name);
      return isCallableType(target, aliases, seen);
    }

    case 'TSIntersectionType':
    case 'TSUnionType':
      return type.types.some((part) => isCallableType(part, aliases, seen));

    case 'TSParenthesizedType':
      return isCallableType(type.typeAnnotation, aliases, seen);

    default:
      return false;
  }
}

/** 一个字段是否是回调。 */
function isCallbackMember(member, aliases) {
  // 方法签名 `renderItem(): void` 等价于函数类型的属性。
  if (member.type === 'TSMethodSignature') return true;
  return isCallableType(typeNodeOf(member), aliases);
}

/** 判断一个字段的类别。 */
function kindOf(member, aliases) {
  const named = NAMED_KINDS[member.key.name];
  if (named) return KIND[named];
  if (isCallbackMember(member, aliases)) return KIND.callback;
  return KIND.parameter;
}

/** 按两条规则算出排序键。 */
function sortKeyOf(member, aliases) {
  const optional = member.optional === true;
  return (optional ? 1 : 0) * 10 + kindOf(member, aliases);
}

function describe(member, aliases) {
  const optional = member.optional === true ? 'optional' : 'required';
  return `${optional} ${KIND_NAME[kindOf(member, aliases)]}`;
}

/** 取出接口体里的一级字段，含方法签名。 */
function membersOf(node) {
  return node.body.body.filter(
    (member) =>
      (member.type === 'TSPropertySignature' ||
        member.type === 'TSMethodSignature') &&
      member.key.type === 'Identifier',
  );
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
    // 同文件的类型别名表。Props 通常与别名写在同一个 types.ts 里；跨文件的
    // 别名无法在语法层面解析。
    const aliases = new Map();

    return {
      TSTypeAliasDeclaration(node) {
        aliases.set(node.id.name, node.typeAnnotation);
      },

      TSInterfaceDeclaration(node) {
        // 只检查公开的 Props 接口。
        if (!/Props$/.test(node.id.name)) return;

        let previous = null;

        for (const member of membersOf(node)) {
          const name = member.key.name;

          if (isCallbackMember(member, aliases) && member.optional !== true) {
            context.report({
              node: member,
              messageId: 'callbackRequired',
              data: { name },
            });
          }

          const key = sortKeyOf(member, aliases);
          if (previous !== null && key < previous.key) {
            context.report({
              node: member,
              messageId: 'order',
              data: {
                name,
                actual: describe(member, aliases),
                previous: previous.name,
                previousDesc: describe(previous.member, aliases),
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
