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
 * - 指向上述类型的同文件类型别名，支持别名套别名、泛型实参代入与前向引用；
 * - 方法签名 `renderItem(): void`，但访问器 `get x(): T` / `set x(v: T)` 不是回调；
 * - 交叉类型或联合类型里含函数成员。
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

/** 从接口成员取出类型节点。 */
function typeNodeOf(member) {
  return member.typeAnnotation?.typeAnnotation;
}

/** 方法签名里的访问器不是回调。 */
function isAccessor(member) {
  return member.type === 'TSMethodSignature' && member.kind !== 'method';
}

/**
 * 把一个类型节点里的类型参数替换为实参。只处理常见的引用与容器形式，未覆盖的
 * 分支按原样返回。
 */
function substitute(type, bindings) {
  if (!type || bindings.size === 0) return type;

  switch (type.type) {
    case 'TSTypeReference': {
      if (type.typeName.type !== 'Identifier') return type;
      const bound = bindings.get(type.typeName.name);
      // 只替换没有自带实参的类型参数引用。
      if (bound && !type.typeArguments) return bound;
      return type;
    }
    case 'TSUnionType':
    case 'TSIntersectionType':
      return {
        ...type,
        types: type.types.map((part) => substitute(part, bindings)),
      };
    case 'TSParenthesizedType':
      return {
        ...type,
        typeAnnotation: substitute(type.typeAnnotation, bindings),
      };
    case 'TSArrayType':
      return { ...type, elementType: substitute(type.elementType, bindings) };
    default:
      return type;
  }
}

/**
 * 判断一个类型节点是否为可调用类型。
 *
 * `aliases` 是本文件的类型别名表，把 `type Render = () => void` 这样的引用解析
 * 回函数类型；`bindings` 是当前别名代入的类型参数。
 */
function isCallableType(type, aliases, bindings = new Map(), seen = new Set()) {
  if (!type) return false;

  switch (type.type) {
    case 'TSFunctionType':
    case 'TSConstructorType':
      return true;

    case 'TSTypeReference': {
      // 先看当前代入；再看同文件别名。外部类型无法在语法层面判断。
      if (type.typeName.type !== 'Identifier') return false;
      const name = type.typeName.name;
      const bound = bindings.get(name);
      if (bound) return isCallableType(bound, aliases, bindings, seen);
      if (seen.has(name)) return false;

      const alias = aliases.get(name);
      if (!alias) return false;

      seen.add(name);
      const next = new Map(bindings);
      const params = alias.typeParameters?.params ?? [];
      const args = type.typeArguments?.params ?? [];
      params.forEach((param, index) => {
        const arg = args[index];
        if (arg) next.set(param.name.name, arg);
      });

      return isCallableType(
        substitute(alias.typeAnnotation, next),
        aliases,
        next,
        seen,
      );
    }

    case 'TSIntersectionType':
    case 'TSUnionType':
      return type.types.some((part) =>
        isCallableType(part, aliases, bindings, seen),
      );

    case 'TSParenthesizedType':
      return isCallableType(type.typeAnnotation, aliases, bindings, seen);

    default:
      return false;
  }
}

/** 一个字段是否是回调。 */
function isCallbackMember(member, aliases) {
  if (member.type === 'TSMethodSignature') return !isAccessor(member);
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
    const interfaces = [];

    return {
      TSTypeAliasDeclaration(node) {
        aliases.set(node.id.name, node);
      },

      TSInterfaceDeclaration(node) {
        // 延后到 Program:exit，等整个文件的别名都收集完，才能处理前向引用。
        interfaces.push(node);
      },

      'Program:exit'() {
        for (const node of interfaces) {
          // 只检查公开的 Props 接口。
          if (!/Props$/.test(node.id.name)) continue;

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
        }
      },
    };
  },
};

export default {
  meta: { name: 'props-order' },
  rules: { 'props-order': rule },
};
