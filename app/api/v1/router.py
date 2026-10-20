from fastapi import APIRouter

from app.api.v1 import admin, auth, me, public, trainer

router = APIRouter()
router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(me.router, prefix="/me", tags=["me"])
router.include_router(trainer.router, prefix="/trainer", tags=["trainer"])
router.include_router(public.router, prefix="/public", tags=["public"])
router.include_router(admin.router, prefix="/admin", tags=["admin"])
