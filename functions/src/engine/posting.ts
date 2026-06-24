/**
 * Post slot scheduling. Finds the next free posting slot (11:00 / 14:00 / 17:00
 * by default) for a platform, interpreted in IST (IIM Sirmaur's timezone), that
 * isn't already taken by another scheduled post for that platform.
 */

import { Timestamp } from "firebase-admin/firestore";

const IST_OFFSET_MIN = 330; // Asia/Kolkata = UTC+5:30

interface Slot {
  h: number;
  m: number;
}

function parseSlots(slots: string[]): Slot[] {
  return slots
    .map((s) => {
      const [h, m] = String(s).split(":").map((n) => Number(n));
      return { h: h || 0, m: m || 0 };
    })
    .sort((a, b) => a.h - b.h || a.m - b.m);
}

/**
 * First slot strictly after `nowMs` (in IST wall-clock) that is not in `takenMs`.
 * `takenMs` holds UTC-millisecond slot times already booked for the platform.
 */
export function findNextSlot(
  slots: string[],
  takenMs: Set<number>,
  nowMs: number
): Timestamp {
  const parsed = parseSlots(slots.length ? slots : ["11:00", "14:00", "17:00"]);

  // Calendar day in IST for "now".
  const istNow = new Date(nowMs + IST_OFFSET_MIN * 60000);
  const y = istNow.getUTCFullYear();
  const mo = istNow.getUTCMonth();
  const d = istNow.getUTCDate();

  for (let day = 0; day < 90; day++) {
    for (const sl of parsed) {
      // IST wall-clock -> UTC ms
      const slotMs = Date.UTC(y, mo, d + day, sl.h, sl.m) - IST_OFFSET_MIN * 60000;
      if (slotMs > nowMs && !takenMs.has(slotMs)) {
        return Timestamp.fromMillis(slotMs);
      }
    }
  }
  // Fallback (shouldn't happen): tomorrow.
  return Timestamp.fromMillis(nowMs + 24 * 3600 * 1000);
}
