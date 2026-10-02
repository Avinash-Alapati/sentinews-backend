import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  vus: 5,
  duration: '30s',
  thresholds: {
    http_req_failed: ['rate<0.01'], // http errors should be less than 1%
    http_req_duration: ['p(95)<200', 'max<1000'], // Phase 3 target: p95 < 200ms, max < 1s
    'http_req_duration{route:indices}': ['p(95)<50'],
    'http_req_duration{route:overview}': ['p(95)<50'],
    'http_req_duration{route:search}': ['p(95)<50'],
    'http_req_duration{route:quote}': ['p(95)<50'],
    'http_req_duration{route:healthz}': ['p(95)<50'],
  },
};

const BASE_URL = __ENV.TARGET_URL || 'http://localhost:8000';

export default function () {
  const params = (route) => ({
    headers: {
      'X-Loadtest': 'true',
    },
    tags: {
      route: route,
      name: route,
    },
  });

  // 1. Root & Health Probes
  const resHealth = http.get(`${BASE_URL}/healthz`, params('healthz'));
  check(resHealth, {
    'healthz status is 200': (r) => r.status === 200,
  });

  // 2. Market Indices
  const resIndices = http.get(`${BASE_URL}/api/v1/market/indices`, params('indices'));
  check(resIndices, {
    'market indices status is 200': (r) => r.status === 200,
    'indices has X-Cache header': (r) => r.headers['X-Cache'] !== undefined,
  });

  // 3. Market Overview
  const resOverview = http.get(`${BASE_URL}/api/v1/market/overview`, params('overview'));
  check(resOverview, {
    'market overview status is 200': (r) => r.status === 200,
    'overview has X-Cache header': (r) => r.headers['X-Cache'] !== undefined,
  });

  // 4. In-Memory Local Stock Search (0ms upstream)
  const resSearch = http.get(`${BASE_URL}/api/v1/market/search?query=TCS`, params('search'));
  check(resSearch, {
    'search status is 200': (r) => r.status === 200,
  });

  // 5. Stock Quote
  const resQuote = http.get(`${BASE_URL}/api/v1/market/quote/RELIANCE`, params('quote'));
  check(resQuote, {
    'quote fetched': (r) => r.status === 200 || r.status === 404,
  });

  // 6. Latest News
  const resNews = http.get(`${BASE_URL}/api/v1/news/latest?limit=10`, params('news_latest'));
  check(resNews, {
    'news latest status is 200': (r) => r.status === 200,
  });

  sleep(0.5);
}
