// Sync-scroll math: direct JS port of tmeld/scroll.py (itself an exact
// port of upstream Meld's calc_syncpoint + _sync_vscroll interpolation).
// All units are lines (uniform line height, no soft wrap).

export function calcSyncpoint(value, pageSize, upper) {
  const half = pageSize / 2;
  if (!half) return 0;
  let syncpoint = 0.5 * Math.min(1, value / half);
  const bottomVal = upper - 1.5 * pageSize;
  syncpoint += 0.5 * Math.max(0, (value - bottomVal) / half);
  return syncpoint;
}

// pairChunks: [[tag, startA, endA, startB, endB], ...] oriented master->other
export function interpolateLine(targetLine, masterTotal, otherTotal, pairChunks) {
  let mbegin = 0, mend = masterTotal;
  let obegin = 0, oend = otherTotal;
  for (const [, sa, ea, sb, eb] of pairChunks) {
    if (sa >= targetLine) {
      mend = sa; oend = sb;
      break;
    } else if (ea >= targetLine) {
      mbegin = sa; mend = ea; obegin = sb; oend = eb;
      break;
    } else {
      mbegin = ea; obegin = eb;
    }
  }
  const fraction = (targetLine - mbegin) / ((mend - mbegin) || 1);
  return obegin + fraction * (oend - obegin);
}

export function scrollOffsetForLine(otherLine, otherPage, otherTotal, syncpoint) {
  let val = otherLine - otherPage * syncpoint;
  const maxScroll = Math.max(0, otherTotal - otherPage);
  return Math.floor(Math.min(Math.max(val, 0), maxScroll));
}

// Influence map (PARITY.md §4): after syncing pane 1 it becomes master.
export const SCROLL_INFLUENCE = {
  2: [[1], [0]],
  3: [[1, 2], [0, 2], [1, 0]],
};
