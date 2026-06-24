/**
 * MongoDB connection + typed collection handles (replaces functions/src/lib/setup.ts).
 *
 * One client for the process. `connect()` is awaited once at boot; everything
 * else uses the `col` helpers, which throw if called before connect().
 *
 * Document id conventions:
 *   - config        _id = "settings" | "taskTypes" | "slots" | "platforms" | "points"
 *   - committees    _id = login email
 *   - team          _id = member email
 *   - requests      _id = ObjectId  (exposed to the client as a hex string `id`)
 *   - tasks         _id = ObjectId; `requestId` is the request's hex string
 *   - activityLog   _id = ObjectId
 */

import { MongoClient, Db, Collection, ObjectId, Document } from "mongodb";

let client: MongoClient | null = null;
let db: Db | null = null;

export async function connect(): Promise<Db> {
  if (db) return db;
  const uri = process.env.MONGODB_URI;
  if (!uri) throw new Error("MONGODB_URI is not set.");
  client = new MongoClient(uri);
  await client.connect();
  db = client.db(process.env.MONGODB_DB || "mcc_portal");
  await ensureIndexes(db);
  console.info(`[db] connected to ${db.databaseName}`);
  return db;
}

export async function close(): Promise<void> {
  if (client) await client.close();
  client = null;
  db = null;
}

export function getDb(): Db {
  if (!db) throw new Error("Mongo not connected — call connect() first.");
  return db;
}

/** Typed collection accessors. */
export const col = {
  config: <T extends Document = Document>(): Collection<T> => getDb().collection<T>("config"),
  committees: <T extends Document = Document>(): Collection<T> => getDb().collection<T>("committees"),
  team: <T extends Document = Document>(): Collection<T> => getDb().collection<T>("team"),
  requests: <T extends Document = Document>(): Collection<T> => getDb().collection<T>("requests"),
  tasks: <T extends Document = Document>(): Collection<T> => getDb().collection<T>("tasks"),
  activityLog: <T extends Document = Document>(): Collection<T> => getDb().collection<T>("activityLog"),
};

/** Indexes that back the engine's hot query paths (idempotent). */
async function ensureIndexes(database: Db): Promise<void> {
  await Promise.all([
    database.collection("requests").createIndex({ contactEmail: 1 }),
    database.collection("requests").createIndex({ coordinatorEmail: 1 }),
    database.collection("requests").createIndex({ status: 1 }),
    database.collection("tasks").createIndex({ requestId: 1 }),
    database.collection("tasks").createIndex({ email: 1 }),
    database.collection("tasks").createIndex({ coordinatorEmail: 1 }),
    database.collection("tasks").createIndex({ status: 1 }),
    database.collection("tasks").createIndex({ vertical: 1 }),
  ]);
}

/** Build an ObjectId from a hex string, or null if it isn't a valid id. */
export function oid(id: string): ObjectId | null {
  return ObjectId.isValid(id) ? new ObjectId(id) : null;
}

export { ObjectId };
