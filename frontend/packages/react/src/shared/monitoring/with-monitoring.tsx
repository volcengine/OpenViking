import { readData } from './data.js';
import { MonitoringFlow, useMonitoringFlow } from './flow-context.js';
import {
  Component,
  createElement,
  forwardRef,
  useContext,
  useInsertionEffect,
  useState,
} from 'react';
import type { ElementType, ErrorInfo, ReactNode } from 'react';
import type { ComponentName } from '../../client/monitoring/source.js';
import {
  OwnerContext,
  useMonitoringController,
  useRenderedMonitoring,
} from './context.js';
import { MonitoringFrame } from './frame.js';
import { MonitoringController } from './controller.js';

class Capture extends Component<
  { frame: MonitoringFrame; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown, info: ErrorInfo) {
    this.props.frame.renderError(error, info.componentStack);
    // Preserve the ancestor's existing fallback and retry lifecycle.
    throw error;
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

/** Preserve the original callable/ref type; no monitoring Props are added. */
export function withMonitoring<C extends ElementType>(
  View: C,
  name: ComponentName,
): C {
  const Monitored = forwardRef<unknown, Record<string, unknown>>(
    (props, ref) => {
      const controller = useMonitoringController();
      const enabled = useRenderedMonitoring();
      const flow = useMonitoringFlow();
      const ancestor = useContext(OwnerContext);
      const [frame] = useState(
        () =>
          new MonitoringFrame(
            name,
            controller ?? new MonitoringController(),
            ancestor,
          ),
      );
      const independent =
        name === 'Console' || name === 'FileBrowser' || name === 'ScopeCascade';
      const inputs =
        !independent &&
        controller?.options &&
        controller === ancestor?.controller
          ? readData(ancestor, Object.values(props))
          : [];
      // Publish this committed component/flow to the external monitoring receiver;
      // discarded renders cannot change its source. Dependency cleanup marks the
      // old binding inactive before callbacks can report against stale Props.
      useInsertionEffect(() => {
        frame.observeContext(enabled, controller);
        if (controller && frame.controller !== controller) {
          frame.leaveScope();
          frame.controller = controller;
        }
        frame.ancestor = ancestor;
        const related = inputs.length
          ? inputs
          : independent
            ? undefined
            : flow?.spans;
        frame.parent = related?.length === 1 ? related[0] : undefined;
        frame.related = related;
        frame.inputs = () => readData(frame, Object.values(props));
        frame.live = true;
        return () => {
          frame.live = false;
        };
      }, [frame, controller, ancestor, flow, props, inputs, enabled]);
      // Release this component's external trace histories and diagnostic bodies
      // on instance removal; there is no matching UI event. A new frame owns its
      // own lifetime, while updates above retain the current instance's history.
      useInsertionEffect(() => () => frame.dispose(), [frame]);
      return (
        <OwnerContext.Provider value={frame}>
          <MonitoringFlow
            spans={independent || inputs.length ? inputs : undefined}
          >
            <Capture frame={frame}>
              {createElement(View, { ...props, ref })}
            </Capture>
          </MonitoringFlow>
        </OwnerContext.Provider>
      );
    },
  );
  Monitored.displayName = name;
  return Monitored as unknown as C;
}
