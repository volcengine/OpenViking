import { antigravity } from "./antigravity.mjs";
import { codebuddy } from "./codebuddy.mjs";
import { cursor } from "./cursor.mjs";
import { kimicode } from "./kimicode.mjs";
import { trae, traeCn } from "./trae.mjs";
import { zcode } from "./zcode.mjs";

/** Every client id the installer can configure, keyed the way it names them. */
export const HOSTS = { antigravity, codebuddy, cursor, kimicode, trae, "trae-cn": traeCn, zcode };
