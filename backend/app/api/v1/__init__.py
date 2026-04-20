from fastapi import APIRouter

from app.api.v1 import auth, forecasts, health, logos, mobile, news, portfolio, live_portfolio, prices, screener, signals, settings, broker_creds, ws, webhooks
from app.api.v1.admin import users as admin_users
from app.api.v1.admin import pipeline as admin_pipeline
from app.api.v1.admin import browser as admin_browser

router = APIRouter(prefix="/api/v1")
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(logos.router)
router.include_router(admin_users.router)
router.include_router(admin_pipeline.router)
router.include_router(admin_browser.router)
router.include_router(prices.router)
router.include_router(screener.router)
router.include_router(signals.router)
router.include_router(settings.router)
router.include_router(broker_creds.router)
router.include_router(news.router)
router.include_router(portfolio.router)
router.include_router(live_portfolio.router)
router.include_router(forecasts.router)
router.include_router(ws.router)
router.include_router(webhooks.router)
router.include_router(mobile.router)
