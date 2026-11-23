import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { StickScroll } from "../lib/stickScroll";

/** Keeps a scrolling element at its newest content while the reader is at the bottom, and lets them scroll away freely.
 * `dep` changes whenever the content does; `enabled` should be false while the element does not exist yet. */
export function useStickScroll(getEl: () => HTMLElement | null | undefined, dep: unknown, enabled = true) {
  const ss = useRef<StickScroll | null>(null);
  const [following, setFollowing] = useState(true);
  useEffect(() => {
    const el = getEl();
    if (!el || !enabled) return;
    const s = new StickScroll(el, 16, setFollowing);
    ss.current = s;
    s.follow();
    return () => { s.dispose(); ss.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);
  useLayoutEffect(() => { ss.current?.follow(); }, [dep]);
  const jump = useCallback(() => ss.current?.jump(), []);
  return { following, jump };
}
