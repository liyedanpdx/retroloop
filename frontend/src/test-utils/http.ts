/**
 * A fake HTTP transport for the frontend tests (#13).
 *
 * It replaces the axios *adapter*, which is the last thing before the network,
 * so everything above it — both clients, the bearer header, the refresh-and-
 * retry interceptor — is the real code under test. Mocking `axios` itself would
 * mock away the interceptors, which are most of what this issue is.
 *
 * No mock-server dependency, per the issue's constraints. An unstubbed request
 * throws rather than falling through to a real network call.
 */
import { AxiosError, AxiosHeaders, type AxiosAdapter, type AxiosResponse } from "axios";

import { api, authClient } from "../api/client";

export type Call = {
  method: string;
  url: string;
  body: unknown;
  authorization: string | undefined;
  withCredentials: boolean | undefined;
};

export type Reply = { status: number; data?: unknown };
export type Handler = (call: Call, index: number) => Reply | Promise<Reply>;

export function installHttp(routes: Record<string, Handler | Reply>): Call[] {
  const calls: Call[] = [];

  const adapter: AxiosAdapter = async (config) => {
    const headers = AxiosHeaders.from(config.headers as never);
    const call: Call = {
      method: (config.method ?? "get").toUpperCase(),
      url: config.url ?? "",
      body: typeof config.data === "string" ? JSON.parse(config.data) : config.data,
      authorization: headers.get("Authorization") as string | undefined,
      withCredentials: config.withCredentials,
    };
    calls.push(call);

    const key = `${call.method} ${call.url}`;
    const route = routes[key];
    if (!route) {
      throw new Error(`unstubbed request: ${key}`);
    }

    const seen = calls.filter((earlier) => `${earlier.method} ${earlier.url}` === key).length - 1;
    const reply = typeof route === "function" ? await route(call, seen) : route;

    const response = {
      data: reply.data ?? {},
      status: reply.status,
      statusText: "",
      headers: {},
      config,
    } as AxiosResponse;

    if (reply.status >= 400) {
      throw new AxiosError("failed", String(reply.status), config, {}, response);
    }
    return response;
  };

  api.defaults.adapter = adapter;
  authClient.defaults.adapter = adapter;
  return calls;
}

export const ALICE = {
  id: "u1",
  email: "alice@example.com",
  display_name: "Alice",
  created_at: "2026-01-01T00:00:00Z",
};

export function callsTo(calls: Call[], method: string, url: string): Call[] {
  return calls.filter((call) => call.method === method && call.url === url);
}
