"""
Fast Route Template Resolver.

Extracts route templates (e.g. '/api/v1/portfolio/{id}') from FastAPI routes
and compiles parameterized regexes to map raw URLs to templates at O(N) where N is small (<50),
preventing high-cardinality metric label explosions.
"""

import re
from typing import List, Optional, Pattern, Tuple
from fastapi import FastAPI
from fastapi.routing import APIRoute


class RouteMatcher:
    """
    Matches raw request paths against compiled route templates.
    """

    def __init__(self):
        self._patterns: List[Tuple[Pattern, str, str]] = []  # (regex, template, method)
        self._exact_templates: List[Tuple[str, str]] = []  # (exact_path, method)

    def initialize_from_app(self, app: FastAPI) -> None:
        """
        Inspects all routes registered on the FastAPI app and builds matcher tables.
        """
        patterns: List[Tuple[Pattern, str, str]] = []
        exact_templates: List[Tuple[str, str]] = []

        for route in app.routes:
            if isinstance(route, APIRoute):
                template = route.path
                methods = route.methods or {"GET"}

                # Check if template has parameters like {id}, {symbol}
                if "{" in template:
                    # Convert {param} or {param:path} to regex group ([^/]+)
                    regex_str = re.sub(r"\{[^}]+\}", r"([^/]+)", template)
                    # Handle optional trailing slash
                    regex_pattern = f"^{regex_str}/?$"
                    compiled = re.compile(regex_pattern)
                    for method in methods:
                        patterns.append((compiled, template, method.upper()))
                else:
                    norm_template = template.rstrip("/") or "/"
                    for method in methods:
                        exact_templates.append((norm_template, method.upper()))

        # Sort patterns by complexity/length descending so more specific matches win
        patterns.sort(key=lambda item: len(item[1]), reverse=True)

        self._patterns = patterns
        self._exact_templates = exact_templates

    def match(self, path: str, method: str = "GET") -> str:
        """
        Matches a raw path and method to its registered route template.
        Returns 'unmatched' if no route matches.
        """
        method_upper = method.upper()
        norm_path = path.rstrip("/") or "/"

        # 1. Exact match check
        for template, meth in self._exact_templates:
            if meth == method_upper and template == norm_path:
                return template

        # 2. Parameterized pattern check
        for pattern, template, meth in self._patterns:
            if meth == method_upper and pattern.match(norm_path):
                return template

        # 3. Method-agnostic fallback check for exact template
        for template, _ in self._exact_templates:
            if template == norm_path:
                return template

        # 4. Method-agnostic fallback check for patterns
        for pattern, template, _ in self._patterns:
            if pattern.match(norm_path):
                return template

        return "unmatched"


# Standardized Feature Name Mapping for Observability & Analytics Dashboards
ROUTE_FEATURE_MAP = {
    "/api/v1/market-reports/pre-market/latest": "market_reports_pre_market",
    "/api/v1/market-reports/post-market/latest": "market_reports_post_market",
    "/api/v1/market-reports/global/pre-market/latest": "market_reports_global_pre_market",
    "/api/v1/market-reports/global/post-market/latest": "market_reports_global_post_market",
    "/api/v1/market-reports/{report_id}": "market_reports_get",
    "/api/v1/market-reports/{id}": "market_reports_get",
    "/api/v1/market-reports": "market_reports_list",
    "/internal/market-reports/generate": "market_reports_generate",
    "/api/v1/news": "news_feed",
    "/api/v1/news/{id}/click": "news_click",
    "/api/v1/portfolio": "portfolio_overview",
}


def get_feature_for_route(template: str) -> str:
    """Returns the standardized feature name for an endpoint route template."""
    return ROUTE_FEATURE_MAP.get(template, template.replace("/", "_").strip("_") or "root")


# Global singleton instance
route_matcher = RouteMatcher()
