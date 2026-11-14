"""inspection endpoints. Owned by the be-voice session (see CONTRACT.md)."""
from fastapi import APIRouter

router = APIRouter(prefix="/api/inspection", tags=["inspection"])
