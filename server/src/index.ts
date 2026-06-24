/**
 * =============================================================================
 * Media Committee Portal — Express + MongoDB server (replaces Cloud Functions).
 * =============================================================================
 * Boot order: load env -> connect Mongo -> configure passport -> build the app
 * (cors, session, passport, routers) -> listen. Mongo must be connected before
 * the session store (connect-mongo) and any route handler runs.
 */

import "dotenv/config";
import express from "express";
import cors from "cors";

import { connect } from "./db";
import { sessionMiddleware } from "./auth/session";
import { configurePassport, passport } from "./auth/passport";
import { authRouter } from "./routes/auth";
import { configRouter } from "./routes/config";
import { requestsRouter } from "./routes/requests";
import { tasksRouter } from "./routes/tasks";
import { assignmentsRouter } from "./routes/assignments";
import { teamRouter } from "./routes/team";
import { dashboardRouter } from "./routes/dashboard";
import { cronRouter } from "./routes/cron";

async function main(): Promise<void> {
  await connect();
  configurePassport();

  const app = express();

  // Render (and most PaaS) terminate TLS at a proxy; trust it so secure cookies work.
  app.set("trust proxy", 1);

  app.use(
    cors({
      origin: process.env.CLIENT_ORIGIN || "http://localhost:3000",
      credentials: true,
    })
  );
  app.use(express.json({ limit: "1mb" }));
  app.use(sessionMiddleware());
  app.use(passport.initialize());
  app.use(passport.session());

  app.get("/api/health", (_req, res) => {
    res.json({ ok: true, service: "mcc-portal-server", time: new Date().toISOString() });
  });

  app.use(authRouter);
  app.use(configRouter);
  app.use(requestsRouter);
  app.use(tasksRouter);
  app.use(assignmentsRouter);
  app.use(teamRouter);
  app.use(dashboardRouter);
  app.use(cronRouter);

  // Centralized error handler.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  app.use((err: any, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    const status = Number(err?.status) || 500;
    const message = err?.expose ? err.message : status === 500 ? "Internal error" : err.message;
    if (status === 500) console.error("[error]", err);
    res.status(status).json({ error: message || "Internal error" });
  });

  const port = Number(process.env.PORT) || 8080;
  app.listen(port, () => console.info(`[server] listening on :${port}`));
}

main().catch((err) => {
  console.error("Fatal startup error:", err);
  process.exit(1);
});
