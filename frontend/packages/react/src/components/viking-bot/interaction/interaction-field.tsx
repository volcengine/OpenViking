import type { ReactNode } from 'react';
import { isRetainedFieldRenderer } from './field-renderer.js';
import { useViewActive } from '../../../shared/view-activity.js';
import { FormField } from '../../../shared/primitives/form-field/form-field.js';
import type {
  VikingBotInteractionField,
  VikingBotInteractionFieldContext,
} from './field-types.js';

export function InteractionField<Value>({
  config,
  value,
  onChange,
  disabled,
  children,
  ...props
}: {
  config?: VikingBotInteractionField<Value>;
  value: Value;
  onChange: (value: Value) => void;
  disabled: boolean;
  id: string;
  label?: ReactNode;
  description?: ReactNode;
  error?: ReactNode;
  group?: boolean;
  children: (
    control: VikingBotInteractionFieldContext<Value>['controlProps'],
  ) => ReactNode;
}) {
  const active = useViewActive();
  return (
    <FormField
      {...props}
      label={config?.label ?? props.label}
      description={config?.description ?? props.description}
    >
      {(controlProps) =>
        config?.render
          ? (active || isRetainedFieldRenderer(config.render)) &&
            config.render({
              value,
              onChange,
              disabled,
              error: props.error,
              controlProps,
            })
          : children(controlProps)
      }
    </FormField>
  );
}
