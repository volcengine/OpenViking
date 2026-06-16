export type HttpTransport = (url: string, init: RequestInit) => Promise<Response>;

export const defaultHttpTransport: HttpTransport = (url, init) => fetch(url, init);
