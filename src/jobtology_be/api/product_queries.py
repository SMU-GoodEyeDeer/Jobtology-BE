from fastapi import HTTPException

from jobtology_be.application.queries import ProductQueries


async def require_product_queries() -> ProductQueries:
    raise HTTPException(status_code=503)
