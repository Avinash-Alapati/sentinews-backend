import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '10s', target: 5 },   // Normal load
    { duration: '20s', target: 120 }, // Sudden 10x+ spike
    { duration: '1m', target: 120 },  // Maintain spike
    { duration: '20s', target: 5 },   // Sudden recovery
    { duration: '10s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<2000'],
  },
};

const BASE_URL = __ENV.TARGET_URL || 'http://localhost:8000';

export default function () {
  const params = {
    headers: {
      'X-Loadtest': 'true',
    },
  };

  const res = http.get(`${BASE_URL}/api/v1/market/overview`, params);
  check(res, {
    'overview responded': (r) => r.status === 200,
  });
  sleep(0.2);
}
