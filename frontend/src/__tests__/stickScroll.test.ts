import { describe, expect, it } from "vitest";
import { StickScroll, type ScrollEl } from "../lib/stickScroll";

/** A scrolling box with a settable position, standing in for the DOM element. Moving it does not fire the scroll event
 * by itself, like a browser that has not dispatched the event yet. */
function box(height = 500) {
  const handlers: Array<() => void> = [];
  const el = {
    scrollTop: 1000 - height, scrollHeight: 1000, clientHeight: height,   // starts at the bottom, as the hook does
    addEventListener: (_: string, h: () => void) => handlers.push(h),
    removeEventListener: (_: string, h: () => void) => { handlers.splice(handlers.indexOf(h), 1); },
  };
  return { el: el as unknown as ScrollEl & { scrollTop: number; scrollHeight: number }, fire: () => handlers.slice().forEach((h) => h()), handlers };
}
const grow = (b: ReturnType<typeof box>, by: number) => { b.el.scrollHeight += by; };

describe("StickScroll", () => {
  it("follows new content while the reader is at the bottom", () => {
    const b = box(); const s = new StickScroll(b.el);
    for (let i = 0; i < 5; i++) { grow(b, 100); s.follow(); }
    expect(b.el.scrollTop).toBe(1500);
    expect(s.following).toBe(true);
  });

  it("lets the reader scroll up while content keeps arriving, even before the scroll event is delivered", () => {
    const b = box(); const s = new StickScroll(b.el);
    grow(b, 100); s.follow();                       // at the bottom: 600
    b.el.scrollTop = 200;                           // the reader scrolls up ...
    grow(b, 100); s.follow();                       // ... and a streamed token arrives before the scroll event fires
    expect(b.el.scrollTop).toBe(200);               // not dragged back down
    expect(s.following).toBe(false);
    b.fire();
    grow(b, 100); s.follow();
    expect(b.el.scrollTop).toBe(200);
  });

  it("keeps the position through a long stream once the reader has moved away", () => {
    const b = box(); const s = new StickScroll(b.el);
    b.el.scrollTop = 100; b.fire();
    for (let i = 0; i < 50; i++) { grow(b, 40); s.follow(); }
    expect(b.el.scrollTop).toBe(100);
  });

  it("resumes following when the reader scrolls back to the bottom", () => {
    const b = box(); const s = new StickScroll(b.el);
    b.el.scrollTop = 100; b.fire(); expect(s.following).toBe(false);
    b.el.scrollTop = b.el.scrollHeight - 500 - 4; b.fire();      // within the threshold of the bottom
    expect(s.following).toBe(true);
    grow(b, 200); s.follow();
    expect(b.el.scrollTop).toBe(b.el.scrollHeight);
  });

  it("does not resume while the reader is only part of the way down", () => {
    const b = box(); const s = new StickScroll(b.el);
    b.el.scrollTop = 100; b.fire();
    b.el.scrollTop = 300; b.fire();                  // scrolled down, but still 200px from the bottom
    expect(s.following).toBe(false);
  });

  it("does not take shrinking content for the reader scrolling up", () => {
    const b = box(); const s = new StickScroll(b.el);
    grow(b, 400); s.follow();
    b.el.scrollHeight -= 300; b.el.scrollTop = b.el.scrollHeight - 500;    // content shrank, the browser clamped the position
    b.fire();
    expect(s.following).toBe(true);
  });

  it("jump() goes to the bottom and follows again", () => {
    const b = box(); const s = new StickScroll(b.el);
    b.el.scrollTop = 0; b.fire(); expect(s.following).toBe(false);
    s.jump();
    expect(s.following).toBe(true);
    expect(b.el.scrollTop).toBe(b.el.scrollHeight);
  });

  it("reports changes and stops listening when disposed", () => {
    const b = box(); const seen: boolean[] = []; const s = new StickScroll(b.el, 16, (f) => seen.push(f));
    b.el.scrollTop = 0; b.fire(); s.jump();
    expect(seen).toEqual([false, true]);
    s.dispose(); expect(b.handlers.length).toBe(0);
  });
});
