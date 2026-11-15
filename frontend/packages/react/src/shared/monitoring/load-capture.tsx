import { Component } from 'react';
import type { ReactNode } from 'react';

/** Attribute a rejected lazy import only when this exact loader is committed. */
export class LoadCapture extends Component<
  { caught: (error: unknown) => void; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown) {
    this.props.caught(error);
    throw error;
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}
