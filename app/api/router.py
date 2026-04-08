from fastapi import APIRouter

from app.api.v1.endpoints import complexes

api_router = APIRouter(tags=["api"])
api_router.include_router(complexes.router, prefix="/v1/complexes")
