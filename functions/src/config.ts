/** Loads engine config from the `config` collection. */

import { db } from "./lib/setup";
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

export async function getPoints(): Promise<PointsConfig> {
  const snap = await db.doc("config/points").get();
  return { ...DEFAULT_POINTS, ...(snap.exists ? (snap.data() as PointsConfig) : {}) };
}

export async function getSettings(): Promise<Settings> {
  const snap = await db.doc("config/settings").get();
  return (snap.data() as Settings) ?? ({} as Settings);
}

export async function getTaskTypes(): Promise<TaskType[]> {
  const snap = await db.doc("config/taskTypes").get();
  return (snap.data()?.types as TaskType[]) ?? [];
}

export async function getPlatforms(): Promise<PlatformRow[]> {
  const snap = await db.doc("config/platforms").get();
  return (snap.data()?.platforms as PlatformRow[]) ?? [];
}

export async function getSlots(): Promise<string[]> {
  const snap = await db.doc("config/slots").get();
  return (snap.data()?.slots as string[]) ?? [];
}
