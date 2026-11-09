from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
from app.services.cache import get_revenue_summary
from app.core.auth import authenticate_request as get_current_user

router = APIRouter()

@router.get("/dashboard/summary")
async def get_dashboard_summary(
    property_id: str,
    # March 2024 is the month the clients disputed, and the only month in the seed.
    month: int = 3,
    year: int = 2024,
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="month must be 1-12")
    if year < 2000 or year > 2100:
        raise HTTPException(status_code=400, detail="year is out of range")

    tenant_id = getattr(current_user, "tenant_id", "default_tenant") or "default_tenant"
    
    revenue_data = await get_revenue_summary(property_id, tenant_id, month, year)
    
    total_revenue_float = float(revenue_data['total'])
    
    return {
        "property_id": revenue_data['property_id'],
        "total_revenue": total_revenue_float,
        "currency": revenue_data['currency'],
        "reservations_count": revenue_data['count'],
        "month": revenue_data.get("month", month),
        "year": revenue_data.get("year", year),
        "timezone": revenue_data.get("timezone"),
    }
