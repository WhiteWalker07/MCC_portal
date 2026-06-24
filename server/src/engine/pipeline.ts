/**
 * Pipeline builder — turns a request + task-type config into the list of tasks
 * to assign. Ported from functions/src/engine/pipeline.ts; `Timestamp` → `Date`.
 *
 *   Coverage => Event Coordinator + requested shoot roles, then DERIVE Photo
 *               Editor (if Photographer) and Video Editor (if Videographer).
 *   Post     => Vetter.
 *
 * Deadlines: Coverage deliverables are measured from event end (+ slaHours);
 * at-event roles (slaHours 0) are due at event end; Post (no event) from now.
 */

import { PipelineTask, PointsConfig, RequestDoc, TaskType } from "../types";
import { basePointsFor } from "./points";

const DERIVED_EDITOR: Record<string, string> = {
  Photographer: "Photo Editor",
  Videographer: "Video Editor",
};

const HOUR_MS = 3_600_000;

export function buildPipeline(
  request: RequestDoc,
  taskTypes: TaskType[],
  now: Date,
  pointsCfg: PointsConfig
): PipelineTask[] {
  const byName = new Map(taskTypes.map((t) => [t.task, t]));
  const names: string[] = [];

  if (request.type === "Coverage") {
    names.push("Event Coordinator");
    const roles = Array.isArray(request.rolesNeeded) ? request.rolesNeeded : [];
    for (const r of roles) {
      if (byName.has(r) && !names.includes(r)) names.push(r);
    }
    for (const r of roles) {
      const derived = DERIVED_EDITOR[r];
      if (derived && byName.has(derived) && !names.includes(derived)) {
        names.push(derived);
      }
    }
  } else {
    names.push("Vetter");
  }

  const pipeline: PipelineTask[] = [];
  for (const name of names) {
    const tt = byName.get(name);
    if (!tt) continue; // config missing this task type — skip defensively
    pipeline.push({
      task: tt.task,
      requiredSkill: tt.requiredSkill,
      points: basePointsFor(tt.task, pointsCfg),
      slaHours: tt.slaHours,
      atEvent: tt.atEvent,
      vertical: tt.vertical || "",
      deadline: computeDeadline(tt, request, now),
    });
  }
  return pipeline;
}

export function computeDeadline(tt: TaskType, request: RequestDoc, now: Date): Date | null {
  if (request.type === "Coverage" && request.eventEnd) {
    const end = new Date(request.eventEnd);
    if (tt.atEvent && tt.slaHours === 0) return end;
    return new Date(end.getTime() + tt.slaHours * HOUR_MS);
  }
  // Post, or Coverage missing an end time: measure from now.
  return new Date(now.getTime() + tt.slaHours * HOUR_MS);
}
