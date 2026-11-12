import { act } from "react";
import type { ReactElement } from "react";
import { createRoot } from "react-dom/client";
import type { Root } from "react-dom/client";

export interface RenderResult {
  container: HTMLDivElement;
  root: Root;
  unmount: () => void;
}

export function render(element: ReactElement): RenderResult {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(element);
  });
  return {
    container,
    root,
    unmount: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

/** Flush pending promises and the effects they schedule. */
export async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

export function click(element: Element): void {
  act(() => {
    (element as HTMLElement).click();
  });
}

export async function clickAsync(element: Element): Promise<void> {
  await act(async () => {
    (element as HTMLElement).click();
  });
  await flush();
}

export function setValue(
  element: HTMLInputElement | HTMLTextAreaElement,
  value: string,
): void {
  const prototype =
    element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
  act(() => {
    descriptor?.set?.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

export function choose(element: HTMLSelectElement, value: string): void {
  const descriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
  act(() => {
    descriptor?.set?.call(element, value);
    element.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

export function pressEscape(element: Element): void {
  act(() => {
    element.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  });
}

export function buttonContaining(container: ParentNode, text: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll("button")).find((candidate) =>
    (candidate.textContent ?? "").includes(text),
  );
  if (!button) throw new Error(`No button containing "${text}"`);
  return button as HTMLButtonElement;
}

export function buttonExact(container: ParentNode, text: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll("button")).find(
    (candidate) => (candidate.textContent ?? "").trim() === text,
  );
  if (!button) throw new Error(`No button labeled "${text}"`);
  return button as HTMLButtonElement;
}

export function textOf(container: ParentNode): string {
  return (container.textContent ?? "").replace(/\s+/g, " ").trim();
}
