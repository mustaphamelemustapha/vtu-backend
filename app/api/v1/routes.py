from fastapi import APIRouter
from app.api.v1.endpoints import auth, wallet, data, transactions, admin, services, notifications, dashboard, security, referrals, webhooks

router = APIRouter()

router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(wallet.router, prefix="/wallet", tags=["wallet"])
router.include_router(data.router, prefix="/data", tags=["data"])
router.include_router(transactions.router, prefix="/transactions", tags=["transactions"])
router.include_router(services.router, prefix="/services", tags=["services"])
router.include_router(admin.router, prefix="/admin", tags=["admin"])
router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
router.include_router(security.router, prefix="/security", tags=["security"])
router.include_router(referrals.router, prefix="/referrals", tags=["referrals"])
router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
