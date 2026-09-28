"""
Builds the FastMCP app exposing Varthaai's business-analysis tools, wrapped
in the bearer-token gate from bizmcp/auth.py. See bizmcp/asgi.py for the
process entrypoint (uvicorn bizmcp.asgi:application).

Every tool here is a thin async wrapper around a plain function in
tools.py/sql_tool.py — Django ORM access is sync, so each call is offloaded
to a worker thread (asyncio.to_thread) rather than blocking the server's
single event loop, same pattern as debugger/agent.py's db_query_ro.

Run as a single process (no multiple uvicorn workers) — FastMCP's
streamable-http session state lives in this process's memory.
"""
import asyncio
from typing import Optional

from mcp.server.fastmcp import FastMCP

from bizmcp import sql_tool, tools
from bizmcp.auth import BearerTokenMiddleware, StripMcpTrailingSlashMiddleware

mcp = FastMCP('varthaai-analytics', stateless_http=True)


async def _run(fn, /, **kwargs):
    return await asyncio.to_thread(fn, **kwargs)


@mcp.tool()
async def describe_schema() -> dict:
    """Reference for the tables/columns run_readonly_sql can query. Read
    this before writing raw SQL so column names are exact."""
    return await _run(tools.describe_schema)


@mcp.tool()
async def dashboard_overview(brand_id: Optional[int] = None, days: int = 30) -> dict:
    """Revenue, order counts and AOV for B2C + B2B combined over the last
    `days` days (default 30). brand_id omitted = all brands combined."""
    return await _run(tools.dashboard_overview, brand_id=brand_id, days=days)


@mcp.tool()
async def sales_trend(brand_id: Optional[int] = None, period: str = 'day', days: int = 90) -> dict:
    """B2C revenue/order-count bucketed by 'day', 'week', or 'month' over
    the last `days` days (default 90)."""
    return await _run(tools.sales_trend, brand_id=brand_id, period=period, days=days)


@mcp.tool()
async def top_products(brand_id: Optional[int] = None, days: int = 90, limit: int = 10) -> dict:
    """Best-selling flavors by revenue across B2C + B2B over the last `days`
    days (default 90), top `limit` (default 10, max 50)."""
    return await _run(tools.top_products, brand_id=brand_id, days=days, limit=limit)


@mcp.tool()
async def stock_status(brand_id: Optional[int] = None) -> dict:
    """Current available stock per flavor, open low/out-of-stock alerts,
    and batches expiring within 30 days."""
    return await _run(tools.stock_status, brand_id=brand_id)


@mcp.tool()
async def b2b_pipeline_summary(brand_id: Optional[int] = None) -> dict:
    """B2B company counts/value by pipeline stage (lead/contacted/
    negotiation/converted/lost) and overall conversion rate."""
    return await _run(tools.b2b_pipeline_summary, brand_id=brand_id)


@mcp.tool()
async def b2b_outstanding(brand_id: Optional[int] = None, limit: int = 20) -> dict:
    """B2B outstanding / overdue / advance-credit totals, plus the top
    `limit` companies (default 20) by amount outstanding. Always computed
    as total_amount - paid_amount, never the stored balance_amount."""
    return await _run(tools.b2b_outstanding, brand_id=brand_id, limit=limit)


@mcp.tool()
async def b2b_report(brand_id: Optional[int] = None, days: int = 30) -> dict:
    """Full B2B analysis for the last `days` days (default 30): pipeline
    snapshot, flavor sales trends, top/at-risk companies, financial health,
    and flagged issues worth attention. Needs exactly one brand; falls back
    to the first active brand if brand_id is omitted."""
    return await _run(tools.b2b_report, brand_id=brand_id, days=days)


@mcp.tool()
async def customer_insights(brand_id: Optional[int] = None, days: int = 30, limit: int = 10) -> dict:
    """New vs returning B2C customers, total loyalty points outstanding,
    and the top `limit` customers (default 10) by lifetime spend, over the
    last `days` days (default 30)."""
    return await _run(tools.customer_insights, brand_id=brand_id, days=days, limit=limit)


@mcp.tool()
async def coupon_performance(brand_id: Optional[int] = None, days: int = 90) -> dict:
    """Usage count, discount given, and revenue influenced per coupon over
    the last `days` days (default 90)."""
    return await _run(tools.coupon_performance, brand_id=brand_id, days=days)


@mcp.tool()
async def finance_summary(brand_id: Optional[int] = None, days: int = 30) -> dict:
    """Expenses (by category) vs booked Income over the last `days` days
    (default 30) — a profitability view, not just cost tracking."""
    return await _run(tools.finance_summary, brand_id=brand_id, days=days)


@mcp.tool()
async def marketing_source_performance(days: int = 90) -> dict:
    """Traffic by marketing source (QR codes / campaign codes) over the
    last `days` days (default 90)."""
    return await _run(tools.marketing_source_performance, days=days)


@mcp.tool()
async def review_summary(brand_id: Optional[int] = None, days: int = 90) -> dict:
    """Approved review volume and rating distribution over the last `days`
    days (default 90)."""
    return await _run(tools.review_summary, brand_id=brand_id, days=days)


@mcp.tool()
async def run_readonly_sql(sql: str) -> dict:
    """Escape hatch for questions the other tools don't cover: run ONE
    read-only SQL SELECT (or WITH ... SELECT) against the production
    database. Call describe_schema first to check table/column names."""
    return await _run(sql_tool.run_readonly_sql, sql=sql)


def build_asgi_app():
    return BearerTokenMiddleware(StripMcpTrailingSlashMiddleware(mcp.streamable_http_app()))
