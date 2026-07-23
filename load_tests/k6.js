import http from "k6/http";
import { check, fail } from "k6";

const BASE_URL = (__ENV.BASE_URL || "http://localhost:8000").replace(/\/+$/, "");
const TARGET_URL = __ENV.TARGET_URL || "https://example.com/k6-target";

function positiveNumber(name, fallback) {
  const value = Number(__ENV[name] || fallback);
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${name} must be a positive number`);
  }
  return value;
}

function nonNegativeNumber(name, fallback) {
  const value = Number(__ENV[name] || fallback);
  if (!Number.isFinite(value) || value < 0) {
    throw new Error(`${name} must be zero or a positive number`);
  }
  return value;
}

function positiveInteger(name, fallback) {
  const value = positiveNumber(name, fallback);
  if (!Number.isInteger(value)) {
    throw new Error(`${name} must be an integer`);
  }
  return value;
}

function nonNegativeInteger(name, fallback) {
  const value = nonNegativeNumber(name, fallback);
  if (!Number.isInteger(value)) {
    throw new Error(`${name} must be an integer`);
  }
  return value;
}

const READ_RATE = positiveInteger("READ_RATE", 12);
const WRITE_RATE = positiveInteger("WRITE_RATE", 12);
const BURST_READ_RATE = positiveInteger("BURST_READ_RATE", 120);
const BURST_WRITE_RATE = positiveInteger("BURST_WRITE_RATE", 120);
const PRE_ALLOCATED_VUS = positiveInteger("PRE_ALLOCATED_VUS", 200);
const MAX_VUS = positiveInteger("MAX_VUS", 400);
const MAX_READ_MS = positiveNumber("MAX_READ_MS", 500);
const MAX_WRITE_MS = positiveNumber("MAX_WRITE_MS", 1000);
const SEED_URL_COUNT = positiveInteger("SEED_URL_COUNT", 20);
const PRELOADED_ROW_COUNT = nonNegativeInteger("PRELOADED_ROW_COUNT", 0);
const MAX_PRELOADED_ROW_COUNT = 0xfffffffff;

if (MAX_VUS < PRE_ALLOCATED_VUS) {
  throw new Error("MAX_VUS must be greater than or equal to PRE_ALLOCATED_VUS");
}
if (PRELOADED_ROW_COUNT > MAX_PRELOADED_ROW_COUNT) {
  throw new Error(
    `PRELOADED_ROW_COUNT must not exceed ${MAX_PRELOADED_ROW_COUNT}`,
  );
}

const loadStages = (steadyRate, burstRate) => [
  {
    target: steadyRate,
    duration: __ENV.SUSTAINED_DURATION || "2m",
  },
  {
    target: burstRate,
    duration: __ENV.BURST_RAMP_DURATION || "30s",
  },
  {
    target: burstRate,
    duration: __ENV.BURST_DURATION || "30s",
  },
  {
    target: steadyRate,
    duration: __ENV.RECOVERY_DURATION || "30s",
  },
  {
    target: steadyRate,
    duration: __ENV.POST_RECOVERY_DURATION || "30s",
  },
];

export const options = {
  discardResponseBodies: true,
  scenarios: {
    reads: {
      executor: "ramping-arrival-rate",
      exec: "readTraffic",
      startRate: nonNegativeInteger("READ_START_RATE", READ_RATE),
      timeUnit: "1s",
      preAllocatedVUs: PRE_ALLOCATED_VUS,
      maxVUs: MAX_VUS,
      stages: loadStages(READ_RATE, BURST_READ_RATE),
      gracefulStop: "15s",
    },
    writes: {
      executor: "ramping-arrival-rate",
      exec: "writeTraffic",
      startRate: nonNegativeInteger("WRITE_START_RATE", WRITE_RATE),
      timeUnit: "1s",
      preAllocatedVUs: PRE_ALLOCATED_VUS,
      maxVUs: MAX_VUS,
      stages: loadStages(WRITE_RATE, BURST_WRITE_RATE),
      gracefulStop: "15s",
    },
  },
  thresholds: {
    "checks{operation:read}": ["rate>0.999"],
    "checks{operation:write}": ["rate>0.999"],
    "http_req_failed{operation:read}": ["rate<0.001"],
    "http_req_failed{operation:write}": ["rate<0.001"],
    "http_req_duration{operation:read}": ["p(95)<200", "p(99)<500"],
    "http_req_duration{operation:write}": ["p(95)<300", "p(99)<1000"],
    "dropped_iterations{scenario:reads}": ["count==0"],
    "dropped_iterations{scenario:writes}": ["count==0"],
  },
};

function createShortUrl(targetUrl, tags = {}) {
  return http.post(
    `${BASE_URL}/shorten`,
    JSON.stringify({ url: targetUrl }),
    {
      headers: { "Content-Type": "application/json" },
      redirects: 0,
      responseType: "text",
      tags: {
        name: "POST /shorten",
        operation: "write",
        ...tags,
      },
    },
  );
}

function shortCodeFrom(response) {
  try {
    const shortUrl = response.json("short_url");
    return shortUrl.replace(/[?#].*$/, "").replace(/\/+$/, "").split("/").pop();
  } catch (_error) {
    return "";
  }
}

export function setup() {
  if (PRELOADED_ROW_COUNT > 0) {
    return { codes: [], preloadedRowCount: PRELOADED_ROW_COUNT };
  }

  const codes = [];

  for (let index = 0; index < SEED_URL_COUNT; index += 1) {
    const response = createShortUrl(
      `${TARGET_URL}?seed=${Date.now()}-${index}`,
      { operation: "setup", setup: "true" },
    );
    const code = shortCodeFrom(response);

    const created = check(response, {
      "setup write returned 2xx": (result) =>
        result.status >= 200 && result.status < 300,
      "setup write returned a short URL": () => code.length > 0,
    }, { operation: "setup" });

    if (!created) {
      fail(
        `Unable to seed short URL ${index + 1}/${SEED_URL_COUNT} ` +
          `(status ${response.status})`,
      );
    }

    codes.push(code);
  }

  return { codes, preloadedRowCount: 0 };
}

function preloadedCode(rowNumber) {
  return `P${rowNumber.toString(16).padStart(9, "0")}`;
}

export function readTraffic(data) {
  const code = data.preloadedRowCount > 0
    ? preloadedCode(
        ((__VU * 1000003 + __ITER) % data.preloadedRowCount) + 1,
      )
    : data.codes[(__VU + __ITER) % data.codes.length];
  const response = http.get(`${BASE_URL}/${code}`, {
    redirects: 0,
    tags: {
      name: "GET /:code",
      operation: "read",
    },
  });

  check(
    response,
    {
      "read request met its SLO": (result) =>
        [301, 302, 303, 307, 308].includes(result.status)
        && result.timings.duration <= MAX_READ_MS,
    },
    { operation: "read" },
  );
}

export function writeTraffic() {
  const uniqueTarget =
    `${TARGET_URL}?vu=${__VU}&iteration=${__ITER}&timestamp=${Date.now()}`;
  const response = createShortUrl(uniqueTarget);

  check(
    response,
    {
      "write request met its SLO": (result) =>
        result.status >= 200
        && result.status < 300
        && shortCodeFrom(result).length > 0
        && result.timings.duration <= MAX_WRITE_MS,
    },
    { operation: "write" },
  );
}
