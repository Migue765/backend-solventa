from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class CoberturaProducto:
    nombre: str
    descripcion: str


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
    coberturas: tuple[CoberturaProducto, ...]
    condiciones: tuple[str, ...]
    vigencia: str


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
        coberturas=(
            CoberturaProducto(
                "Daños al vehículo",
                "Protección ante daños accidentales sufridos por el vehículo asegurado.",
            ),
            CoberturaProducto(
                "Responsabilidad civil",
                "Respaldo frente a daños causados a terceros durante la conducción.",
            ),
            CoberturaProducto(
                "Asistencia en carretera",
                "Acompañamiento ante inmovilización o emergencia durante el trayecto.",
            ),
        ),
        condiciones=(
            "El vehículo debe estar registrado y cumplir las condiciones de asegurabilidad.",
            "El deducible se aplica por cada evento cubierto.",
        ),
        vigencia="Anual con renovación",
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
        coberturas=(
            CoberturaProducto(
                "Asistencia médica",
                "Atención por accidente o enfermedad durante el viaje.",
            ),
            CoberturaProducto(
                "Equipaje",
                "Respaldo ante pérdida o demora del equipaje durante el trayecto.",
            ),
            CoberturaProducto(
                "Demoras y cancelaciones",
                "Compensación por eventos cubiertos que afecten el itinerario.",
            ),
        ),
        condiciones=(
            "La cobertura aplica durante las fechas y destinos informados.",
            "Los eventos deben reportarse dentro de los plazos definidos en la póliza.",
        ),
        vigencia="Por la duración del viaje",
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
        coberturas=(
            CoberturaProducto(
                "Fallecimiento",
                "Respaldo económico para los beneficiarios ante el fallecimiento del asegurado.",
            ),
            CoberturaProducto(
                "Incapacidad total y permanente",
                "Protección económica cuando un evento cubierto impide continuar trabajando.",
            ),
        ),
        condiciones=(
            "La edad del asegurado debe encontrarse dentro del rango aceptado.",
            "La información de salud debe ser completa y verificable.",
        ),
        vigencia="Mensual renovable",
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
        coberturas=(
            CoberturaProducto(
                "Daño accidental",
                "Reparación o reposición por daños accidentales cubiertos.",
            ),
            CoberturaProducto(
                "Hurto calificado",
                "Respaldo por pérdida del dispositivo bajo las condiciones contratadas.",
            ),
            CoberturaProducto(
                "Falla funcional",
                "Atención de fallas cubiertas posteriores al periodo de garantía.",
            ),
        ),
        condiciones=(
            "El dispositivo debe identificarse y encontrarse en buen estado al asegurarlo.",
            "El valor declarado debe corresponder al valor de compra verificable.",
        ),
        vigencia="Anual con renovación",
    ),
    ProductoSeguro(
        producto_id="parametric",
        nombre="Seguro de Vuelos",
        descripcion="Compensación automática ante retrasos o cancelaciones de vuelos.",
        disponible=True,
        producto_interno_id=UUID("99999999-9999-4999-8999-999999999999"),
        cobertura_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        suma_asegurada=Decimal("1500000"),
        deducible=Decimal("0"),
        coberturas=(
            CoberturaProducto(
                "Retraso de vuelo",
                "Compensación cuando el retraso supera el umbral contratado.",
            ),
            CoberturaProducto(
                "Cancelación",
                "Compensación ante la cancelación de un vuelo cubierto.",
            ),
        ),
        condiciones=(
            "El vuelo debe registrarse antes de la hora programada de salida.",
            "La confirmación se basa en la información oficial de la aerolínea.",
        ),
        vigencia="Por vuelo",
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
        coberturas=(
            CoberturaProducto(
                "Desempleo involuntario",
                "Pago temporal de obligaciones cuando ocurre un desempleo cubierto.",
            ),
            CoberturaProducto(
                "Incapacidad temporal",
                "Respaldo de cuotas durante una incapacidad cubierta.",
            ),
        ),
        condiciones=(
            "La obligación financiera debe estar vigente y al día al contratar.",
            "La cobertura está sujeta al tipo de crédito y al valor de la cuota.",
        ),
        vigencia="Mensual renovable",
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
        coberturas=(
            CoberturaProducto(
                "Fallecimiento",
                "Pago del saldo asegurado del crédito hipotecario.",
            ),
            CoberturaProducto(
                "Incapacidad total y permanente",
                "Protección del saldo hipotecario ante una incapacidad cubierta.",
            ),
        ),
        condiciones=(
            "La suma asegurada se ajusta al saldo vigente del crédito.",
            "La continuidad depende de la vigencia de la obligación hipotecaria.",
        ),
        vigencia="Durante la vigencia del crédito",
    ),
)


def buscar_producto(producto_id: str) -> ProductoSeguro | None:
    return next((producto for producto in PRODUCTOS if producto.producto_id == producto_id), None)
