import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '30s', target: 20 },  // Ramp up to 20 users
    { duration: '1m', target: 50 },   // Ramp up to 50 users
    { duration: '1m', target: 100 },  // Push to 100 users (SLA target validation)
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<200', 'max<1000'],
    'http_req_duration{route:overview}': ['p(95)<200'],
    'http_req_duration{route:quote}': ['p(95)<200'],
    'http_req_duration{route:search}': ['p(95)<200'],
    'http_req_duration{route:news}': ['p(95)<200'],
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

  const stockSymbols = ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'TATAMOTORS'];
  const sym = stockSymbols[Math.floor(Math.random() * stockSymbols.length)];

  // 1. Overview
  http.get(`${BASE_URL}/api/v1/market/overview`, params('overview'));

  // 2. Quote lookup
  const resQuote = http.get(`${BASE_URL}/api/v1/market/quote/${sym}`, params('quote'));
  check(resQuote, {
    'quote fetched': (r) => r.status === 200 || r.status === 404,
  });

  // 3. In-Memory Search
  http.get(`${BASE_URL}/api/v1/market/search?query=${sym}`, params('search'));

  // 4. News search
  const resNews = http.get(`${BASE_URL}/api/v1/news/latest?symbol=${sym}`, params('news'));
  check(resNews, {
    'news fetched': (r) => r.status === 200,
  });

  sleep(0.5);
}
