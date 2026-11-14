// Keeps one broken section from blanking the whole dashboard (e.g. a value the server left null).
import { Component, type ReactNode } from 'react';

export class SectionBoundary extends Component<{ name: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidUpdate(prev: { children: ReactNode }) {
    // New data may fix it: try again when the section's props change.
    if (this.state.failed && prev.children !== this.props.children) this.setState({ failed: false });
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="card flex min-h-[120px] items-center justify-center p-5">
        <span className="text-sm text-ink-3">{this.props.name}: waiting for data</span>
      </div>
    );
  }
}
