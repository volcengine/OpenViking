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
 * 一个身份字段的作用域标识。字符串直接用值；函数按函数对象本身取编号——同一个
 * 闭包每次请求求值出同样的凭证，而换了闭包就意味着换了一份凭证来源。
 *
 * The scope identity of one identity field. A string uses its value; a function uses
 * the function object's own id — the same closure yields the same credential on every
 * request, while a different closure means a different credential source.
 */
function fieldKey(value: OvIdentityValue | undefined): string {
  if (value === undefined) return '';
  if (typeof value === 'function') return `fn#${idOf(value)}`;
  return value;
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
): string {
  return [
    idOf(client),
    options?.baseURL ?? '',
    fieldKey(options?.apiKey),
    fieldKey(options?.accountId),
    fieldKey(options?.userId),
    fieldKey(options?.resourceId),
  ].join('|');
}

/** 一次请求所属的作用域：键与它的控制器。One request's scope: its key and controller. */
export interface RequestScope {
  key: string;
  controller: AbortController;
}
