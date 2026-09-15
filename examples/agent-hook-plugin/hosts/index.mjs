import { cursor } from "./cursor.mjs";
import { trae, traeCn } from "./trae.mjs";
import { zcode } from "./zcode.mjs";

/** Every client id the installer can configure, keyed the way it names them. */
export const HOSTS = { cursor, trae, "trae-cn": traeCn, zcode };
