import type { VikingBotInteractionField } from './field-types.js';

const retainInactive = Symbol('retain-inactive-field-renderer');
type Renderer<Value> = NonNullable<VikingBotInteractionField<Value>['render']>;
type RetainedRenderer<Value> = Renderer<Value> & { [retainInactive]?: true };

/** Built-in controls own drafts and cooperate with the private activity scope. */
export function retainInactiveFieldRenderer<Value>(render: Renderer<Value>) {
  return Object.assign(render, { [retainInactive]: true as const });
}

export function isRetainedFieldRenderer<Value>(render: Renderer<Value>) {
  return (render as RetainedRenderer<Value>)[retainInactive] === true;
}
