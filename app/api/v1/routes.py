from fastapi import APIRouter
from app.api.v1.endpoints import auth, wallet, data, transactions, admin, services

router = APIRouter()

router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(wallet.router, prefix="/wallet", tags=["wallet"])
router.include_router(data.router, prefix="/data", tags=["data"])
router.include_router(transactions.router, prefix="/transactions", tags=["transactions"])
router.include_router(services.router, prefix="/services", tags=["services"])
router.include_router(admin.router, prefix="/admin", tags=["admin"])
