import { Alert, Button, Stack, Text } from "@mantine/core";
import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Catches a render-time crash (e.g. an API response shape the page didn't
 * expect) so it shows a recoverable error instead of unmounting the whole
 * app to a blank screen. Give it a `key` that changes with the route (see
 * App.tsx) so navigating away resets it.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    console.error("Unhandled error in page render", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <Stack p="md" gap="sm" align="flex-start">
          <Alert color="red" title="Something went wrong loading this page">
            <Text size="sm">{this.state.error.message || "Unknown error"}</Text>
          </Alert>
          <Button variant="light" onClick={() => this.setState({ error: null })}>
            Try again
          </Button>
        </Stack>
      );
    }
    return this.props.children;
  }
}
