// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import readline from 'node:readline';
import { list, t } from './i18n.mjs';
import { SYMBOLS, padEnd, rowsOf, spinner, styler, width } from './ui.mjs';

// Terminal questions for `vika setup`. On a TTY: arrow keys move, space ticks
// a box, enter confirms, and an answered question folds into one line. On a
// colour terminal the questions are drawn with symbols and colour. Without a
// TTY (piped input): numbered choices read one line at a time, plain text, so
// a script can still answer.
//
// While `navigable` is on, ← (or Esc) leaves a question with GoBack, for the
// caller to go back a step, and → answers it as Enter would. In a text field
// both only act while nothing is typed, so typing is never interrupted.

const ESC = '\x1b[';

const NOTHING_TYPED = () => t('没有收到输入，再输一次（每个字符会显示成 *）', 'Nothing came through; type it again (each character shows as *)');
const atLeast = (min) => t(`至少选 ${min} 项`, `Choose at least ${min}`);

export class PromptAborted extends Error {
  constructor() {
    super('setup cancelled');
    this.name = 'PromptAborted';
  }
}

// `notice`, when given, says why: the caller shows it where it goes back to.
export class GoBack extends Error {
  constructor(notice = null) {
    super('back');
    this.name = 'GoBack';
    this.notice = notice;
  }
}

export class Prompter {
  constructor({ input = process.stdin, output = process.stdout, env = process.env } = {}) {
    this.input = input;
    this.output = output;
    this.env = env;
    this.tty = Boolean(input.isTTY && typeof input.setRawMode === 'function');
    this.s = styler(output, env);
    // Drawn questions need both ends to be a terminal, with colour.
    this.fancy = this.tty && Boolean(output.isTTY) && this.s.enabled;
    this.lines = null;
    this.transcript = [];
    // The first row of the questions' region, when something above it is
    // pinned (setup's banner); null when the questions just scroll.
    this.pageTop = null;
    this.navigable = false;
    // How many questions have been asked, so a caller can tell whether a
    // step asked anything (going back skips steps that only informed).
    this.asked = 0;
    if (this.tty) readline.emitKeypressEvents(input);
  }

  get columns() {
    return this.output.columns || 80;
  }

  // What stays on screen is written and recorded, so the questions can be
  // drawn again after going back (rewind) or after another program took the
  // screen (redraw). A question being answered is only drawn.
  write(text) {
    this.output.write(text);
    this.transcript.push(text);
  }

  draw(text) {
    this.output.write(text);
  }

  mark() {
    return this.transcript.length;
  }

  // Takes the screen back to how it was at `mark`, as if what came after
  // had never been shown. With a known top row (the questions' region below
  // the pinned banner) it clears the region and draws the record again;
  // otherwise it clears the rows written since, or the screen when they no
  // longer fit on it, or when `whole` says the screen holds more than the
  // record (a code to scan was drawn there).
  rewind(mark, { whole = false } = {}) {
    const removed = this.transcript.splice(mark).join('');
    if (!this.fancy) return;
    const top = this.pageTop?.();
    if (top) {
      this.draw(`${ESC}${top};1H${ESC}J${this.transcript.join('')}`);
      return;
    }
    if (whole) {
      this.draw(`${ESC}H${ESC}J${this.transcript.join('')}`);
      return;
    }
    if (!removed) return;
    const rows = rowsOf(removed.endsWith('\n') ? removed.slice(0, -1) : removed, this.columns) - (removed.endsWith('\n') ? 0 : 1);
    if (rows < (this.output.rows ?? 24)) this.draw(`\r${rows > 0 ? `${ESC}${rows}A` : ''}${ESC}J`);
    else this.draw(`${ESC}H${ESC}J${this.transcript.join('')}`);
  }

  // The record drawn again from the top of the questions' region.
  redraw() {
    const top = this.fancy && this.pageTop?.();
    if (top) this.draw(`${ESC}${top};1H${ESC}J${this.transcript.join('')}`);
  }

  // A heading for a part of the flow, with its step when there are several.
  section(title, { step, total } = {}) {
    if (!this.fancy) {
      this.write(`\n── ${title} ──\n`);
      return;
    }
    const counter = step ? ` ${this.s.gray(`${step}/${total}`)}` : '';
    const used = width(title) + (step ? width(` ${step}/${total}`) : 0) + 5;
    const rule = '━'.repeat(Math.max(4, Math.min(this.columns, 64) - used));
    this.write(`\n${this.s.gradient('━━')} ${this.s.bold(title)}${counter} ${this.s.gradient(rule)}\n`);
  }

  info(text) {
    this.write(this.fancy ? `  ${this.s.gray(SYMBOLS.info)} ${text}\n` : `  ${text}\n`);
  }

  warn(text) {
    this.write(this.fancy ? `  ${this.s.yellow(SYMBOLS.warn)} ${text}\n` : `  ⚠ ${text}\n`);
  }

  success(text) {
    this.write(this.fancy ? `  ${this.s.green(SYMBOLS.done)} ${text}\n` : `  ${text}\n`);
  }

  // A spinner while something runs; see ui.spinner.
  spin(text) {
    return spinner(this.output, text, { env: this.env, indent: '  ', commit: (line) => this.write(line) });
  }

  async nextLine() {
    this.lines ??= readline.createInterface({ input: this.input, terminal: false })[Symbol.asyncIterator]();
    const { value, done } = await this.lines.next();
    if (done) throw new PromptAborted();
    return value.trim();
  }

  // Resolves with the result of onKey once it returns something other than
  // undefined; raw mode is held only for the duration of one question.
  keys(onKey, { back = true } = {}) {
    return new Promise((resolve, reject) => {
      const finish = (error, value) => {
        this.input.off('keypress', handler);
        this.input.setRawMode(false);
        this.input.pause();
        if (error) reject(error); else resolve(value);
      };
      const handler = (str, key = {}) => {
        if (key.ctrl && key.name === 'c') { finish(new PromptAborted()); return; }
        if (this.navigable && back && key.name === 'escape') { finish(new GoBack()); return; }
        try {
          const value = onKey(str, key);
          if (value !== undefined) finish(null, value);
        } catch (error) {
          finish(error);
        }
      };
      this.input.setRawMode(true);
      this.input.resume();
      this.input.on('keypress', handler);
    });
  }

  // While something else runs: resolves true once `key` is pressed, false
  // when stop() is called first. Keys in `also` run their callback and keep
  // watching, and so does `onBack` for ← and Esc. Without a TTY it never fires.
  watchKey(key, { also = {}, onBack = null } = {}) {
    if (!this.tty) return { pressed: new Promise(() => {}), stop() {} };
    let settle;
    const pressed = new Promise((resolve) => { settle = resolve; });
    const handler = (str, info = {}) => {
      if (info.ctrl && info.name === 'c') { stop(); process.kill(process.pid, 'SIGINT'); return; }
      if (str === key) { settle(true); stop(); return; }
      if (onBack && (info.name === 'left' || info.name === 'escape')) { onBack(); return; }
      if (str && also[str]) also[str]();
    };
    const stop = () => {
      this.input.off('keypress', handler);
      this.input.setRawMode(false);
      this.input.pause();
      settle(false);
    };
    this.input.setRawMode(true);
    this.input.resume();
    this.input.on('keypress', handler);
    return { pressed, stop };
  }

  // ← in navigable mode: leave the question. Otherwise the key does nothing.
  back() {
    if (this.navigable) throw new GoBack();
  }

  // Clears the rows just drawn (the cursor is on the last of them, or just
  // below them after a newline) and writes the folded answer line.
  fold(rows, question, answer, { below = false } = {}) {
    const up = below ? rows : rows - 1;
    this.draw(`\r${up > 0 ? `${ESC}${up}A` : ''}${ESC}J`);
    this.write(`${this.s.green(SYMBOLS.done)} ${question} ${this.s.gray('·')} ${this.s.accent(answer)}\n`);
  }

  // A question left with ←: it disappears, and the caller rewinds to where
  // it goes back to.
  abandon(error, rows, question, { below = false } = {}) {
    if (!(error instanceof GoBack)) return;
    if (!this.fancy) {
      if (!below) this.draw('\n');
      return;
    }
    const up = below ? rows : rows - 1;
    this.draw(`\r${up > 0 ? `${ESC}${up}A` : ''}${ESC}J`);
  }

  // One * per character, so typing or a paste is visibly received; asks
  // again rather than accept nothing.
  async secret(question) {
    if (!this.tty) return this.text(question);
    this.asked += 1;
    for (;;) {
      const prompt = this.fancy ? `${this.s.accent(SYMBOLS.active)} ${this.s.bold(question)} ${this.s.accent('›')} ` : `? ${question}: `;
      this.draw(prompt);
      let buffer = '';
      const value = await this.keys((str, key) => {
        if (key.name === 'left' && !buffer) return this.back();
        if (key.name === 'return' || key.name === 'enter') {
          if (!this.fancy) this.draw('\n');
          return buffer.trim();
        }
        if (key.name === 'backspace') {
          if (buffer) { buffer = [...buffer].slice(0, -1).join(''); this.draw('\b \b'); }
          return undefined;
        }
        if (str && !key.ctrl && !key.meta && str >= ' ') { buffer += str; this.draw('*'.repeat([...str].length)); }
        return undefined;
      }).catch((error) => {
        this.abandon(error, rowsOf(prompt, this.columns), question);
        throw error;
      });
      if (this.fancy) {
        const rows = rowsOf(`${prompt}${'*'.repeat([...buffer].length)}`, this.columns);
        if (value) {
          const count = [...value].length;
          this.fold(rows, question, this.s.gray(`${'•'.repeat(Math.min(12, count))}${t(`（${count} 个字符）`, ` (${count} characters)`)}`));
          return value;
        }
        this.fold(rows, question, '');
        this.write(`  ${this.s.yellow(SYMBOLS.warn)} ${NOTHING_TYPED()}\n`);
        continue;
      }
      if (value) return value;
      this.write(`  ${NOTHING_TYPED()}\n`);
    }
  }

  // `label` is the question's short name for its folded line.
  async text(question, { initial = '', validate = () => null, label = question } = {}) {
    this.asked += 1;
    for (;;) {
      const hint = initial ? ` (${initial})` : '';
      const prompt = this.fancy
        ? `${this.s.accent(SYMBOLS.active)} ${this.s.bold(question)}${this.s.gray(hint)} ${this.s.accent('›')} `
        : `? ${question}${hint}: `;
      this.draw(prompt);
      let answer;
      if (this.tty) {
        let buffer = '';
        answer = await this.keys((str, key) => {
          if (key.name === 'left' && !buffer) return this.back();
          // → with nothing typed takes the value in brackets, as Enter does.
          const forward = key.name === 'right' && !buffer && this.navigable;
          if (forward || key.name === 'return' || key.name === 'enter') {
            if (!this.fancy) this.draw('\n');
            return buffer;
          }
          if (key.name === 'backspace') {
            if (buffer) {
              const last = [...buffer].at(-1);
              buffer = [...buffer].slice(0, -1).join('');
              const columns = width(last);
              this.draw(`${'\b'.repeat(columns)}${' '.repeat(columns)}${'\b'.repeat(columns)}`);
            }
            return undefined;
          }
          if (str && !key.ctrl && !key.meta && str >= ' ') { buffer += str; this.draw(str); }
          return undefined;
        }).catch((error) => {
          this.abandon(error, rowsOf(prompt, this.columns), label);
          throw error;
        });
      } else {
        answer = await this.nextLine();
        this.draw('\n');
      }
      const value = answer.trim() || initial;
      const problem = validate(value);
      if (this.fancy) {
        const rows = rowsOf(`${prompt}${answer}`, this.columns);
        if (!problem) {
          this.fold(rows, label, value || this.s.gray(t('（空）', '(empty)')));
          return value;
        }
        this.fold(rows, label, this.s.red(answer.trim() || t('（空）', '(empty)')));
        this.write(`  ${this.s.yellow(SYMBOLS.warn)} ${problem}\n`);
        continue;
      }
      if (!problem) return value;
      this.write(`  ${problem}\n`);
    }
  }

  async confirm(question, initial = true) {
    if (this.fancy) {
      this.asked += 1;
      const hint = initial ? 'Y/n' : 'y/N';
      const prompt = `${this.s.accent(SYMBOLS.active)} ${this.s.bold(question)} ${this.s.gray(`(${hint})`)} ${this.s.accent('›')} `;
      this.draw(prompt);
      const answer = await this.keys((str, key) => {
        if (key.name === 'left') return this.back();
        if (key.name === 'return' || key.name === 'enter' || (key.name === 'right' && this.navigable)) return initial;
        if (/^y$/i.test(str ?? '')) return true;
        if (/^n$/i.test(str ?? '')) return false;
        return undefined;
      }).catch((error) => {
        this.abandon(error, rowsOf(prompt, this.columns), question);
        throw error;
      });
      this.fold(rowsOf(prompt, this.columns), question, answer ? t('是', 'yes') : t('否', 'no'));
      return answer;
    }
    const answer = await this.text(`${question} ${initial ? '[Y/n]' : '[y/N]'}`, {
      validate: (value) => (value === '' || /^(y|yes|n|no)$/i.test(value) ? null : t('请输入 y 或 n', 'Type y or n')),
    });
    return answer === '' ? initial : /^y/i.test(answer);
  }

  // choices: [{ label, value, hint?, disabled? }]. `back: false` for a
  // question with nothing before it: ← and Esc do nothing there.
  async select(question, choices, { initial = 0, label, back } = {}) {
    const [value] = await this.pick(question, choices, { multiple: false, initial: [initial], label, back });
    return value;
  }

  async multiselect(question, choices, { initial = [], min = 1, label } = {}) {
    return this.pick(question, choices, { multiple: true, initial, min, label });
  }

  async pick(question, choices, { multiple, initial = [], min = 1, label = question, back = true }) {
    const enabled = choices.map((choice, index) => (choice.disabled ? -1 : index)).filter((index) => index >= 0);
    if (enabled.length === 0) throw new Error(`${question}: nothing to choose from`);
    const selected = new Set(initial.filter((index) => enabled.includes(index)));
    this.asked += 1;
    if (!this.tty) return this.pickByNumber(question, choices, { multiple, selected, min });
    if (this.fancy) return this.pickDrawn(question, choices, { multiple, selected, min, enabled, initial, label, back });

    let cursor = enabled.includes(initial[0]) ? initial[0] : enabled[0];
    const help = multiple ? t('（↑↓ 移动，空格勾选，回车确认）', ' (↑↓ move, space ticks, Enter confirms)')
      : t('（↑↓ 移动，回车确认）', ' (↑↓ move, Enter confirms)');
    const lines = () => choices.map((choice, index) => {
      const pointer = index === cursor ? '❯' : ' ';
      const box = multiple ? (selected.has(index) ? '[x] ' : '[ ] ') : '';
      const hint = choice.hint ? `  ${choice.hint}` : '';
      return choice.disabled ? `  ${box}${choice.label}${hint}` : `${pointer} ${box}${choice.label}${hint}`;
    });
    this.draw(`? ${question}${help}\n${ESC}?25l${lines().join('\n')}\n`);
    const redraw = (note = '') => {
      this.draw(`${ESC}${choices.length + (redraw.noted ? 1 : 0)}A${ESC}0J${lines().join('\n')}\n${note ? `  ${note}\n` : ''}`);
      redraw.noted = Boolean(note);
    };
    const move = (step) => {
      const position = enabled.indexOf(cursor);
      cursor = enabled[(position + step + enabled.length) % enabled.length];
    };
    try {
      return await this.keys((str, key) => {
        if (key.name === 'up' || key.name === 'k') { move(-1); redraw(); return undefined; }
        if (key.name === 'down' || key.name === 'j') { move(1); redraw(); return undefined; }
        if (key.name === 'left') return back ? this.back() : undefined;
        if (multiple && key.name === 'space') {
          if (selected.has(cursor)) selected.delete(cursor); else selected.add(cursor);
          redraw();
          return undefined;
        }
        if (key.name === 'return' || key.name === 'enter' || (key.name === 'right' && this.navigable)) {
          if (!multiple) return [choices[cursor].value];
          if (selected.size < min) { redraw(atLeast(min)); return undefined; }
          return [...selected].sort((a, b) => a - b).map((index) => choices[index].value);
        }
        return undefined;
      }, { back });
    } finally {
      this.draw(`${ESC}?25h`);
    }
  }

  // The colour version of a choice list: labels in a column, hints beside
  // them, unavailable rows dimmed; once answered it folds into one line.
  async pickDrawn(question, choices, { multiple, selected, min, enabled, initial, label: short, back }) {
    const { s } = this;
    let cursor = enabled.includes(initial[0]) ? initial[0] : enabled[0];
    let note = '';
    const labelWidth = Math.max(...choices.map((choice) => width(choice.label)));
    const help = s.gray(t(
      `↑↓ 移动${multiple ? ' · 空格勾选' : ''} · 回车确认${this.navigable && back ? ' · ← 返回' : ''}`,
      `↑↓ move${multiple ? ' · space ticks' : ''} · enter confirms${this.navigable && back ? ' · ← back' : ''}`,
    ));
    const block = () => {
      const rows = choices.map((choice, index) => {
        const here = index === cursor;
        const mark = multiple
          ? (selected.has(index) ? s.accent(SYMBOLS.boxOn) : s.gray(SYMBOLS.boxOff))
          : (here ? s.accent(SYMBOLS.radioOn) : s.gray(SYMBOLS.radioOff));
        const label = padEnd(choice.label, labelWidth);
        const hint = choice.hint ? `  ${s.gray(choice.hint)}` : '';
        if (choice.disabled) return `    ${s.gray(`${SYMBOLS.radioOff} ${label}`)}${hint}`;
        return `  ${here ? s.accent(SYMBOLS.pointer) : ' '} ${mark} ${here ? s.bold(s.accent(label)) : label}${hint}`;
      });
      return [`${s.accent(SYMBOLS.active)} ${s.bold(question)}  ${help}`, ...rows, ...(note ? [`  ${s.yellow(SYMBOLS.warn)} ${note}`] : [])].join('\n');
    };
    let drawn = block();
    this.draw(`${ESC}?25l${drawn}\n`);
    const redraw = () => {
      const rows = rowsOf(drawn, this.columns);
      drawn = block();
      this.draw(`${ESC}${rows}A${ESC}J${drawn}\n`);
    };
    const move = (step) => {
      const position = enabled.indexOf(cursor);
      cursor = enabled[(position + step + enabled.length) % enabled.length];
    };
    try {
      const values = await this.keys((str, key) => {
        if (key.name === 'up' || key.name === 'k') { move(-1); note = ''; redraw(); return undefined; }
        if (key.name === 'down' || key.name === 'j') { move(1); note = ''; redraw(); return undefined; }
        if (key.name === 'left') return back ? this.back() : undefined;
        if (multiple && key.name === 'space') {
          if (selected.has(cursor)) selected.delete(cursor); else selected.add(cursor);
          note = '';
          redraw();
          return undefined;
        }
        if (key.name === 'return' || key.name === 'enter' || (key.name === 'right' && this.navigable)) {
          if (!multiple) return [cursor];
          if (selected.size < min) { note = atLeast(min); redraw(); return undefined; }
          return [...selected].sort((a, b) => a - b);
        }
        return undefined;
      }, { back }).catch((error) => {
        this.abandon(error, rowsOf(drawn, this.columns), short, { below: true });
        throw error;
      });
      this.fold(rowsOf(drawn, this.columns), short, list(values.map((index) => choices[index].label)), { below: true });
      return values.map((index) => choices[index].value);
    } finally {
      this.draw(`${ESC}?25h`);
    }
  }

  async pickByNumber(question, choices, { multiple, selected, min }) {
    this.write(`? ${question}\n`);
    choices.forEach((choice, index) => {
      const hint = choice.hint ? `  ${choice.hint}` : '';
      this.write(`  ${index + 1}) ${choice.label}${hint}${choice.disabled ? t('（不可选）', ' (unavailable)') : ''}\n`);
    });
    const fallback = [...selected].map((index) => index + 1).join(',');
    const answer = await this.text(multiple ? t('输入编号，多个用逗号分隔', 'Numbers, separated by commas') : t('输入编号', 'Number'), {
      initial: fallback,
      validate: (value) => {
        const numbers = value.split(/[\s,，]+/).filter(Boolean).map(Number);
        if (numbers.some((number) => !Number.isInteger(number) || !choices[number - 1] || choices[number - 1].disabled)) {
          return t('编号无效', 'No such choice');
        }
        if (!multiple && numbers.length !== 1) return t('只能选一项', 'Choose one');
        if (numbers.length < min) return atLeast(min);
        return null;
      },
    });
    return [...new Set(answer.split(/[\s,，]+/).filter(Boolean).map(Number))]
      .sort((a, b) => a - b).map((number) => choices[number - 1].value);
  }

  close() {
    this.lines?.return?.();
  }
}
