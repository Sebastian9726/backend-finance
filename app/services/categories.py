"""Categorias de ingreso y gasto."""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import HTTP_422
from app.core.tenant import get_owned_or_404, owned
from app.models import Category, CategoryType
from app.schemas.finance import CategoryCreate, CategoryUpdate

# Set inicial que se siembra al registrarse, pensado para Colombia. El usuario
# puede renombrarlas o borrarlas: son suyas, no un catalogo del sistema.
SEED_CATEGORIES: list[tuple[str, CategoryType, str, str]] = [
    ("Salario", CategoryType.INGRESO, "#16A34A", "wallet"),
    ("Honorarios", CategoryType.INGRESO, "#22C55E", "briefcase"),
    ("Ventas", CategoryType.INGRESO, "#4ADE80", "shopping-bag"),
    ("Arriendos recibidos", CategoryType.INGRESO, "#15803D", "home"),
    ("Rendimientos", CategoryType.INGRESO, "#10B981", "trending-up"),
    ("Otros ingresos", CategoryType.INGRESO, "#6EE7B7", "plus-circle"),
    ("Vivienda", CategoryType.GASTO, "#DC2626", "home"),
    ("Alimentacion", CategoryType.GASTO, "#EA580C", "utensils"),
    ("Transporte", CategoryType.GASTO, "#D97706", "car"),
    ("Servicios publicos", CategoryType.GASTO, "#CA8A04", "zap"),
    ("Salud", CategoryType.GASTO, "#E11D48", "heart-pulse"),
    ("Educacion", CategoryType.GASTO, "#7C3AED", "graduation-cap"),
    ("Entretenimiento", CategoryType.GASTO, "#DB2777", "party-popper"),
    ("Ropa", CategoryType.GASTO, "#9333EA", "shirt"),
    ("Mascotas", CategoryType.GASTO, "#C026D3", "paw-print"),
    ("Impuestos", CategoryType.GASTO, "#78716C", "landmark"),
    ("Deudas y creditos", CategoryType.GASTO, "#B91C1C", "credit-card"),
    ("Ahorro e inversion", CategoryType.GASTO, "#0891B2", "piggy-bank"),
    ("Otros gastos", CategoryType.GASTO, "#94A3B8", "circle-ellipsis"),
]


async def seed_default_categories(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Siembra el set inicial. No hace commit: lo decide quien la llama."""
    db.add_all(
        Category(
            id=uuid.uuid4(),
            user_id=user_id,
            nombre=nombre,
            tipo=tipo,
            color=color,
            icono=icono,
        )
        for nombre, tipo, color, icono in SEED_CATEGORIES
    )


async def list_categories(
    db: AsyncSession, user_id: uuid.UUID, tipo: CategoryType | None = None
) -> list[Category]:
    query = owned(Category, user_id)
    if tipo is not None:
        query = query.where(Category.tipo == tipo)
    result = await db.execute(query.order_by(Category.tipo, Category.nombre))
    return list(result.scalars().all())


async def create_category(
    db: AsyncSession, user_id: uuid.UUID, data: CategoryCreate
) -> Category:
    if data.parent_id is not None:
        padre = await get_owned_or_404(
            db, Category, data.parent_id, user_id, detail="Categoria padre no encontrada"
        )
        if padre.tipo != data.tipo:
            raise HTTPException(
                status_code=HTTP_422,
                detail="La subcategoria debe ser del mismo tipo que su categoria padre",
            )
        if padre.parent_id is not None:
            raise HTTPException(
                status_code=HTTP_422,
                detail="Solo se admiten dos niveles de categoria",
            )

    category = Category(id=uuid.uuid4(), user_id=user_id, **data.model_dump())
    db.add(category)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe una categoria de {data.tipo.value} llamada '{data.nombre}'",
        ) from exc
    await db.refresh(category)
    return category


async def update_category(
    db: AsyncSession, user_id: uuid.UUID, category_id: uuid.UUID, data: CategoryUpdate
) -> Category:
    category = await get_owned_or_404(
        db, Category, category_id, user_id, detail="Categoria no encontrada"
    )

    cambios = data.model_dump(exclude_unset=True)
    if cambios.get("parent_id") == category_id:
        raise HTTPException(
            status_code=HTTP_422,
            detail="Una categoria no puede ser su propia categoria padre",
        )
    for campo, valor in cambios.items():
        setattr(category, campo, valor)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe otra categoria con ese nombre y tipo",
        ) from exc
    await db.refresh(category)
    return category


async def delete_category(db: AsyncSession, user_id: uuid.UUID, category_id: uuid.UUID) -> None:
    """Borra la categoria. Las transacciones que la usaban quedan sin categoria
    (`ON DELETE SET NULL`) en lugar de desaparecer con ella."""
    category = await get_owned_or_404(
        db, Category, category_id, user_id, detail="Categoria no encontrada"
    )
    hijas = await db.execute(select(Category.id).where(Category.parent_id == category_id))
    if hijas.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se puede borrar una categoria que tiene subcategorias",
        )
    await db.delete(category)
    await db.commit()
