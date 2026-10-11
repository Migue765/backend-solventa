from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class ProductoSeguro:
    producto_id: str
    nombre: str
    descripcion: str
    disponible: bool
    producto_interno_id: UUID
    cobertura_id: UUID
    suma_asegurada: Decimal
    deducible: Decimal


PRODUCTOS = (
    ProductoSeguro(
        producto_id="automotive",
        nombre="Seguro Automotriz Total",
        descripcion="Protección para tu vehículo, ocupantes y terceros.",
        disponible=True,
        producto_interno_id=UUID("11111111-1111-4111-8111-111111111111"),
        cobertura_id=UUID("22222222-2222-4222-8222-222222222222"),
        suma_asegurada=Decimal("25000000"),
        deducible=Decimal("500000"),
    ),
    ProductoSeguro(
        producto_id="travel",
        nombre="Seguro de Viaje",
        descripcion="Protección para viajes nacionales e internacionales.",
        disponible=False,
        producto_interno_id=UUID("33333333-3333-4333-8333-333333333333"),
        cobertura_id=UUID("44444444-4444-4444-8444-444444444444"),
        suma_asegurada=Decimal("0"),
        deducible=Decimal("0"),
    ),
    ProductoSeguro(
        producto_id="life",
        nombre="Seguro de Vida",
        descripcion="Respaldo financiero para ti y tu familia.",
        disponible=False,
        producto_interno_id=UUID("55555555-5555-4555-8555-555555555555"),
        cobertura_id=UUID("66666666-6666-4666-8666-666666666666"),
        suma_asegurada=Decimal("0"),
        deducible=Decimal("0"),
    ),
)


def buscar_producto(producto_id: str) -> ProductoSeguro | None:
    return next((producto for producto in PRODUCTOS if producto.producto_id == producto_id), None)
