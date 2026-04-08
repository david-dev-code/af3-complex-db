from fastapi import APIRouter

router = APIRouter()

@router.get("/", summary="Health Check")
async def ping():
    """
    Returns a simple status indicating the service is operational.
    """
    return {"status": "ok"}

