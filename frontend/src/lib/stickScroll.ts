/** Follows the newest content of a scrolling element only while the reader is at the bottom.
 *
 * Scrolling up stops the following at once, even when new content arrives before the browser has reported the scroll
 * (fast streaming): `follow()` compares the element's real scroll position with the one it last set, so the reader's own
 * movement is never overwritten. Scrolling back to the bottom resumes following. Content that shrinks (and the clamp that
 * comes with it) is not mistaken for the reader scrolling up. */
export type ScrollEl = Pick<HTMLElement, "scrollTop" | "scrollHeight" | "clientHeight" | "addEventListener" | "removeEventListener">;

export class StickScroll {
  following = true;
  private lastTop = 0;
  private lastHeight = 0;

  constructor(private el: ScrollEl, private threshold = 16, private onChange?: (following: boolean) => void) {
    el.addEventListener("scroll", this.onScroll, { passive: true });
    this.sync();
  }

  private sync() { this.lastTop = this.el.scrollTop; this.lastHeight = this.el.scrollHeight; }
  private set(v: boolean) { if (v !== this.following) { this.following = v; this.onChange?.(v); } }
  private movedUp() { return this.el.scrollTop < this.lastTop - 1 && this.el.scrollHeight >= this.lastHeight; }
  private atBottom() { return this.el.scrollHeight - this.el.scrollTop - this.el.clientHeight < this.threshold; }

  private onScroll = () => {
    if (this.movedUp()) this.set(false);
    else if (this.atBottom()) this.set(true);
    this.sync();
  };

  /** Call after the content changed: scrolls to the bottom unless the reader has moved away from it. */
  follow() {
    if (this.following && this.movedUp()) this.set(false);          // the reader scrolled up and the scroll event is still on its way
    if (this.following) this.el.scrollTop = this.el.scrollHeight;
    this.sync();
  }

  /** Go to the bottom and follow again (a message was just sent, or the "jump to latest" button was pressed). */
  jump() { this.set(true); this.follow(); }

  dispose() { this.el.removeEventListener("scroll", this.onScroll); }
}
