/**
 * Scoring scheme. Base points per task role + a completion-timing modifier, all
 * driven by config/points so admins can tune it without code changes.
 *
 * Base:
 *   Event Coordinator -> coordinatorPoints
 *   Vetter            -> vetterPoints
 *   every other task  -> domainTaskPoints
 *
 * Timing modifier (applied when the task is completed), where `turnaroundHours`
 * is measured from the event end (coverage) or request creation (post/no-event):
 *   <= earlyWindowHours          -> +earlyBonusPct%
 *   >  lateThresholdHours        -> -(latePenaltyPct + subsequentPenaltyPct per
 *                                     extra block of subsequentDelayHours)%
 *   otherwise                    -> base (no change)
 */

import { PointsConfig } from "../types";

export function basePointsFor(taskName: string, cfg: PointsConfig): number {
  if (taskName === "Event Coordinator") return cfg.coordinatorPoints;
  if (taskName === "Vetter") return cfg.vetterPoints;
  return cfg.domainTaskPoints;
}

/** Multiplier (e.g. 1.30, 0.70) for a given turnaround. */
export function timingMultiplier(turnaroundHours: number, cfg: PointsConfig): number {
  if (turnaroundHours <= cfg.earlyWindowHours) {
    return 1 + cfg.earlyBonusPct / 100;
  }
  if (turnaroundHours > cfg.lateThresholdHours) {
    const extraBlocks = Math.floor(
      (turnaroundHours - cfg.lateThresholdHours) / Math.max(1, cfg.subsequentDelayHours)
    );
    const penaltyPct = cfg.latePenaltyPct + cfg.subsequentPenaltyPct * extraBlocks;
    return Math.max(0, 1 - penaltyPct / 100);
  }
  return 1;
}

/** Final points = round(base * multiplier). */
export function finalPoints(
  base: number,
  turnaroundHours: number,
  cfg: PointsConfig
): number {
  return Math.round(base * timingMultiplier(turnaroundHours, cfg));
}
