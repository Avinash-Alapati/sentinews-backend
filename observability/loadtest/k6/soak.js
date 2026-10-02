import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '2m', target: 30 },  // Ramp up
    { duration: '56m', target: 30 }, // 1-hour sustained soak
    { duration: '2m', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<800'],
  },
};

const BASE_URL = __ENV.TARGET_URL || 'http://localhost:8000';

export default function () {
  const params = {
    headers: {
      'X-Loadtest': 'true',
    },
  };

  const actions = [
    () => http.get(`${BASE_URL}/api/v1/market/overview`, params),
    () => http.get(`${BASE_URL}/api/v1/market/indices`, params),
    () => http.get(`${BASE_URL}/api/v1/news/latest?limit=20`, params),
    () => http.get(`${BASE_URL}/api/v1/news/trending`, params),
  ];

  const action = actions[Math.floor(Math.random() * actions.length)];
  const res = action();
  check(res, {
    'status is 200': (r) => r.status === 200,
  });

  sleep(1);
}
