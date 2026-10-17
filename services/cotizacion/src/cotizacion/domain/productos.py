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
        disponible=True,
        producto_interno_id=UUID("33333333-3333-4333-8333-333333333333"),
        cobertura_id=UUID("44444444-4444-4444-8444-444444444444"),
        suma_asegurada=Decimal("200000000"),
        deducible=Decimal("200000"),
    ),
    ProductoSeguro(
        producto_id="life",
        nombre="Microseguro de Vida",
        descripcion="Respaldo financiero accesible para ti y tu familia.",
        disponible=True,
        producto_interno_id=UUID("55555555-5555-4555-8555-555555555555"),
        cobertura_id=UUID("66666666-6666-4666-8666-666666666666"),
        suma_asegurada=Decimal("30000000"),
        deducible=Decimal("0"),
    ),
    ProductoSeguro(
        producto_id="device-protection",
        nombre="Protección de Dispositivos",
        descripcion="Cobertura para daños, hurto y fallas de tus dispositivos.",
        disponible=True,
        producto_interno_id=UUID("77777777-7777-4777-8777-777777777777"),
        cobertura_id=UUID("88888888-8888-4888-8888-888888888888"),
        suma_asegurada=Decimal("8000000"),
        deducible=Decimal("300000"),
    ),
    ProductoSeguro(
        producto_id="parametric",
        nombre="Seguro de Vuelos",
        descripcion="Protección para retrasos, cancelaciones o pérdida de conexión.",
        disponible=True,
        producto_interno_id=UUID("99999999-9999-4999-8999-999999999999"),
        cobertura_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        suma_asegurada=Decimal("1500000"),
        deducible=Decimal("0"),
    ),
    ProductoSeguro(
        producto_id="parametric-weather",
        nombre="Seguro Paramétrico Climático",
        descripcion="Protección ante lluvia intensa o sequía.",
        disponible=True,
        producto_interno_id=UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
        cobertura_id=UUID("12121212-1212-4212-8212-121212121212"),
        suma_asegurada=Decimal("10000000"),
        deducible=Decimal("0"),
    ),
    ProductoSeguro(
        producto_id="payment-protection",
        nombre="Protección de Pagos",
        descripcion="Respaldo de obligaciones financieras asociadas a créditos.",
        disponible=True,
        producto_interno_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        cobertura_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        suma_asegurada=Decimal("12000000"),
        deducible=Decimal("0"),
    ),
    ProductoSeguro(
        producto_id="mortgage-life",
        nombre="Seguro de Vida Hipotecario",
        descripcion="Protección personalizada para el saldo de tu crédito de vivienda.",
        disponible=True,
        producto_interno_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        cobertura_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        suma_asegurada=Decimal("300000000"),
        deducible=Decimal("0"),
    ),
)


def buscar_producto(producto_id: str) -> ProductoSeguro | None:
    return next((producto for producto in PRODUCTOS if producto.producto_id == producto_id), None)
