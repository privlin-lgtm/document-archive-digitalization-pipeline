import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// vitest.config.ts doesn't set test.globals, so @testing-library/react
// can't auto-detect a global afterEach to register its own cleanup -- every
// render() before this left the previous test's DOM in place, which is
// what made LoginPage's second and later tests in the same file see
// duplicate "Sign in" buttons from the prior test still mounted.
afterEach(() => {
  cleanup();
});

// jsdom doesn't implement matchMedia -- Mantine's color-scheme detection
// calls it unconditionally on mount, so any test rendering a Mantine
// component (MantineProvider included) throws without this polyfill.
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}
