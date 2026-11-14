"""band endpoints. Owned by the be-band session (see CONTRACT.md)."""
from fastapi import APIRouter

router = APIRouter(prefix="/api/band", tags=["band"])
