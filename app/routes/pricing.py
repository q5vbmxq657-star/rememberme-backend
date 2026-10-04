from fastapi import APIRouter, Response
from app.services.pricing_catalog import pricing_catalog

router = APIRouter()


@router.get("/v1/pricing")
def catalog(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return pricing_catalog()
