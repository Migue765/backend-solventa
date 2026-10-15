from dataclasses import dataclass


@dataclass(frozen=True)
class DestinoViaje:
    codigo: str
    nombre: str


DESTINOS_VIAJE = (
    DestinoViaje("AR", "Argentina"),
    DestinoViaje("AU", "Australia"),
    DestinoViaje("BE", "Bélgica"),
    DestinoViaje("BO", "Bolivia"),
    DestinoViaje("BR", "Brasil"),
    DestinoViaje("CA", "Canadá"),
    DestinoViaje("CH", "Suiza"),
    DestinoViaje("CL", "Chile"),
    DestinoViaje("CN", "China"),
    DestinoViaje("CO", "Colombia"),
    DestinoViaje("CR", "Costa Rica"),
    DestinoViaje("DE", "Alemania"),
    DestinoViaje("DO", "República Dominicana"),
    DestinoViaje("EC", "Ecuador"),
    DestinoViaje("ES", "España"),
    DestinoViaje("FR", "Francia"),
    DestinoViaje("GB", "Reino Unido"),
    DestinoViaje("GR", "Grecia"),
    DestinoViaje("GT", "Guatemala"),
    DestinoViaje("IE", "Irlanda"),
    DestinoViaje("IT", "Italia"),
    DestinoViaje("JP", "Japón"),
    DestinoViaje("KR", "Corea del Sur"),
    DestinoViaje("MX", "México"),
    DestinoViaje("NL", "Países Bajos"),
    DestinoViaje("NZ", "Nueva Zelanda"),
    DestinoViaje("PA", "Panamá"),
    DestinoViaje("PE", "Perú"),
    DestinoViaje("PT", "Portugal"),
    DestinoViaje("PY", "Paraguay"),
    DestinoViaje("SE", "Suecia"),
    DestinoViaje("TR", "Turquía"),
    DestinoViaje("US", "Estados Unidos"),
    DestinoViaje("UY", "Uruguay"),
)

CODIGOS_DESTINO_VIAJE = frozenset(destino.codigo for destino in DESTINOS_VIAJE)
