/** Loads engine config from the `config` collection (replaces functions/src/config.ts). */

import { col } from "./db";
import { Settings, TaskType, PlatformRow, PointsConfig } from "./types";

const DEFAULT_POINTS: PointsConfig = {
  coordinatorPoints: 20,
  domainTaskPoints: 10,
  vetterPoints: 10,
  earlyWindowHours: 24,
  earlyBonusPct: 30,
  lateThresholdHours: 48,
  latePenaltyPct: 30,
  subsequentDelayHours: 6,
  subsequentPenaltyPct: 10,
};

async function getConfigDoc(id: string): Promise<Record<string, unknown> | null> {
  return col.config().findOne({ _id: id as never }) as Promise<Record<string, unknown> | null>;
}

export async function getPoints(): Promise<PointsConfig> {
  const doc = await getConfigDoc("points");
  return { ...DEFAULT_POINTS, ...((doc || {}) as Partial<PointsConfig>) };
}

export async function getSettings(): Promise<Settings> {
  const doc = await getConfigDoc("settings");
  return (doc as unknown as Settings) ?? ({} as Settings);
}

export async function getTaskTypes(): Promise<TaskType[]> {
  const doc = await getConfigDoc("taskTypes");
  return ((doc?.types as TaskType[]) ?? []) as TaskType[];
}

export async function getPlatforms(): Promise<PlatformRow[]> {
  const doc = await getConfigDoc("platforms");
  return ((doc?.platforms as PlatformRow[]) ?? []) as PlatformRow[];
}

export async function getSlots(): Promise<string[]> {
  const doc = await getConfigDoc("slots");
  return ((doc?.slots as string[]) ?? []) as string[];
}
