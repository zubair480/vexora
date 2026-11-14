"""exposure endpoints. Owned by the be-hav session (see CONTRACT.md)."""
from fastapi import APIRouter

router = APIRouter(prefix="/api/exposure", tags=["exposure"])
