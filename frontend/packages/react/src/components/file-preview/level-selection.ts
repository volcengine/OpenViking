/**
 * 目录页签的初始层级：受控值优先，其次是非受控时记下的选择，然后是
 * `defaultLevelKey`，最后回落到第一级。
 *
 * 单列成纯函数是因为它有一条容易漏掉的规则：外壳算出的 `activeLevelKey` 是受控
 * 值，正文组件因此没有机会用上自己的初值，`defaultLevelKey` 必须在这里就被尊重，
 * 否则宿主声明的默认层级会被静默改成第一级。
 *
 * The initial level of a directory tab: the controlled value first, then the choice
 * recorded while uncontrolled, then `defaultLevelKey`, and the first level as the last
 * resort.
 *
 * It is a standalone pure function because of one easily missed rule: the
 * `activeLevelKey` the shell computes is a controlled value, so the body component never
 * applies its own initial one — `defaultLevelKey` has to be honoured here, or the
 * host's declared default is silently replaced by the first level.
 */
export interface LevelChoiceTab {
  levels: readonly { key: string }[];
  activeLevelKey?: string;
  defaultLevelKey?: string;
}

export function initialLevelKey(
  tab: LevelChoiceTab,
  remembered: Readonly<Record<string, string>>,
  tabKey: string,
): string {
  const preferred =
    tab.activeLevelKey ?? remembered[tabKey] ?? tab.defaultLevelKey;
  const first = tab.levels[0]?.key ?? '';
  if (preferred === undefined) return first;
  return tab.levels.some((level) => level.key === preferred)
    ? preferred
    : first;
}
