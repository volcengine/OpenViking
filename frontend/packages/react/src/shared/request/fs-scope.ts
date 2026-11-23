import type {
  OvClient,
  OvIdentityValue,
  OvRequestOptions,
} from '../../client/types.js';

/**
 * 给对象分配稳定编号。作用域判定需要「是不是同一个」而不是「内容是否相等」：
 * 客户端是 Provider 每次配置变化才重建的对象，按引用比较即可，但比较结果要能
 * 压成一个可直接放进依赖数组的字符串。
 *
 * Assigns a stable id to an object. Scope checks need "is this the same one" rather
 * than "is the content equal": a client is rebuilt only when the Provider's
 * configuration changes, so reference identity is the right test, but the outcome
 * has to collapse into a string that can sit in a dependency array.
 */
const ids = new WeakMap<object, number>();
let nextId = 1;

function idOf(value: object | null | undefined): number {
  if (!value) return 0;
  const existing = ids.get(value);
  if (existing !== undefined) return existing;
  const assigned = nextId++;
  ids.set(value, assigned);
  return assigned;
}

/**
 * 一个身份字段的作用域标识。
 *
 * 三种状态必须区分开，不能压成同一个空串：
 *
 * - `undefined`（省略）表示**继承**上层配置；
 * - 空字符串表示**显式清除**继承到的值，请求里不再带这个头；
 * - 其余字符串就是值本身。
 *
 * 把「省略」与「显式清除」混为一谈，会在宿主把 `accountId` 从省略改成 `''` 时
 * 认为作用域没变，于是上一个账号在途的响应照旧落到新视图里。
 *
 * 函数按函数对象本身取编号：同一个闭包每次请求求值出同样的凭证，而换了闭包就
 * 意味着换了一份凭证来源。
 *
 * The scope identity of one identity field.
 *
 * Three states must stay distinct rather than collapse into one empty string:
 *
 * - `undefined` (omitted) means **inherit** from the layer above;
 * - an empty string means **explicitly clear** the inherited value, so the header is
 *   no longer sent;
 * - any other string is the value itself.
 *
 * Merging "omitted" with "explicitly cleared" would treat a host switching `accountId`
 * from omitted to `''` as the same scope, so the previous account's in-flight response
 * would still land in the new view.
 *
 * A function uses the function object's own id: the same closure yields the same
 * credential on every request, while a different closure means a different credential
 * source.
 */
function fieldKey(value: OvIdentityValue | undefined): string {
  if (value === undefined) return 'inherit';
  if (typeof value === 'function') return `fn#${idOf(value)}`;
  return `value:${value}`;
}

/**
 * 一次请求的**有效作用域**：客户端实例加上逐请求覆盖的身份字段。
 *
 * 只按客户端实例判定是不够的——宿主可以在同一个 Provider 下把
 * `requestOptions.accountId` 从 A 换成 B，客户端对象不变，但数据空间已经换了；
 * 上一个账号在途的响应同样不能落到新账号的树上。
 *
 * The **effective scope** of one request: the client instance plus the per-request
 * identity overrides.
 *
 * The client instance alone is not enough — a host can switch
 * `requestOptions.accountId` from A to B under the same Provider, leaving the client
 * object unchanged while the data space has changed; a response still in flight for
 * the previous account must not land on the new account's tree either.
 */
export function scopeKeyOf(
  client: OvClient | null,
  options: OvRequestOptions | undefined,
  rootUri?: string,
): string {
  // 用 `JSON.stringify` 编码整个数组，而不是把字段用某个字符拼起来：任何拼接用的
  // 分隔符都可能出现在字段值里，`accountId: 'A|value:B'` 加 `userId: 'C'` 与
  // `accountId: 'A'` 加 `userId: 'B|value:C'` 会拼出同一个键，于是切换账号时旧请求
  // 没有被判成过期。这些值都是宿主可以自由传入的普通字符串。
  //
  // 每个字段本身已经带标签（`inherit` / `value:` / `fn#`），因此编码后的各段边界
  // 是明确的，不需要额外的转义。
  //
  // The whole array is encoded with `JSON.stringify` rather than joined with some
  // character: any joining separator can occur inside a field value, so
  // `accountId: 'A|value:B'` with `userId: 'C'` and `accountId: 'A'` with
  // `userId: 'B|value:C'` would produce the same key and a switch would not mark the
  // old request stale. Those values are ordinary strings a host may pass freely.
  //
  // Each field already carries a tag (`inherit` / `value:` / `fn#`), so the encoded
  // segments have unambiguous boundaries and no extra escaping is needed.
  return JSON.stringify([
    idOf(client),
    // 根目录也属于作用域：换了根就换了一棵树，旧根在途的展开结果不能落进新树。
    // The root is part of the scope too: a different root is a different tree, and an
    // expansion in flight for the old root must not land in the new one.
    rootUri === undefined ? 'inherit-root' : `root:${rootUri}`,
    options?.baseURL === undefined ? 'inherit-base' : `base:${options.baseURL}`,
    fieldKey(options?.apiKey),
    fieldKey(options?.accountId),
    fieldKey(options?.userId),
    fieldKey(options?.resourceId),
  ]);
}

/** 一次请求所属的作用域：键与它的控制器。One request's scope: its key and controller. */
export interface RequestScope {
  key: string;
  controller: AbortController;
}
