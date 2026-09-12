from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import service
from app.cache import ProductCache, get_cache
from app.db import get_session
from app.schemas import ProductCreate, ProductOut, StockAdjustRequest

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=list[ProductOut], summary="List products")
def list_products(
    session: Session = Depends(get_session),
    cache: ProductCache = Depends(get_cache),
) -> list[ProductOut]:
    return service.list_products(session, cache)


@router.get("/{sku}", response_model=ProductOut, summary="One product")
def get_product(
    sku: str,
    session: Session = Depends(get_session),
    cache: ProductCache = Depends(get_cache),
) -> ProductOut:
    return service.get_product(session, sku, cache)


@router.post(
    "", response_model=ProductOut, status_code=status.HTTP_201_CREATED, summary="Create a product"
)
def create_product(
    payload: ProductCreate,
    session: Session = Depends(get_session),
    cache: ProductCache = Depends(get_cache),
) -> ProductOut:
    product = service.create_product(session, payload)
    session.commit()
    # Invalidate after the commit. Dropping the key first leaves a window where a
    # concurrent reader refills it from the row as it was before.
    cache.invalidate(product.sku)
    return ProductOut.model_validate(product)


@router.patch("/{sku}/stock", response_model=ProductOut, summary="Adjust stock")
def adjust_stock(
    sku: str,
    payload: StockAdjustRequest,
    session: Session = Depends(get_session),
    cache: ProductCache = Depends(get_cache),
) -> ProductOut:
    product = service.adjust_stock(session, sku, payload.delta)
    session.commit()
    return ProductOut.model_validate(product)
