// React 19's act() requires this flag outside of React Testing Library.
const globalWithActEnvironment = globalThis as typeof globalThis & {
  IS_REACT_ACT_ENVIRONMENT?: boolean;
};
globalWithActEnvironment.IS_REACT_ACT_ENVIRONMENT = true;

// jsdom does not implement scrollIntoView; MessageList scrolls to the newest
// message after it grows. Keep the DOM shim so tests exercise the component.
if (typeof Element.prototype.scrollIntoView !== "function") {
  Element.prototype.scrollIntoView = () => undefined;
}

// jsdom does not implement matchMedia; useTheme reads it on startup.
if (typeof window.matchMedia !== "function") {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string): MediaQueryList =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        dispatchEvent: () => false,
      }) as MediaQueryList,
  });
}
