"""toolsense endpoints. Owned by the be-toolsense session (see CONTRACT.md)."""
from fastapi import APIRouter

router = APIRouter(prefix="/api/toolsense", tags=["toolsense"])
