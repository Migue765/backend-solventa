from uuid import UUID

POLICY_ID = UUID("6a1d7e3c-90b2-4f3f-8c2e-1b6f0e2d4a55")
ENDORSEMENT_ID = UUID("7b2e8f4d-a1c3-4a40-9d3f-2c7a1f3e5b66")
HASH = "e" * 64
CHANGE: dict[str, object] = {"versionBase": 1, "plan": "premium", "coberturasOpcionales": []}
CONFIRMATION: dict[str, object] = {**CHANGE, "confirma": True, "impactoHash": HASH}


def coverages(included: bool = True) -> list[dict[str, object]]:
    return [
        {
            "codigo": "fallecimiento",
            "nombre": "Fallecimiento",
            "descripcion": "Respaldo economico.",
            "opcional": False,
            "incluida": True,
        },
        {
            "codigo": "incapacidad_total",
            "nombre": "Incapacidad total y permanente",
            "descripcion": "Proteccion por incapacidad.",
            "opcional": True,
            "incluida": included,
        },
    ]


def version(number: int = 1, plan: str = "esencial") -> dict[str, object]:
    return {
        "version": number,
        "plan": plan,
        "coberturas": coverages(number == 1),
        "prima": {
            "monto": "25000" if number == 1 else "24700",
            "moneda": "COP",
            "periodicidad": "mensual",
        },
        "efectivoDesde": "2026-11-11" if number == 1 else "2026-11-16",
        "origen": "emision" if number == 1 else "modificacion",
        "endosoId": None if number == 1 else str(ENDORSEMENT_ID),
        "creadaEn": "2026-11-15T15:00:00Z",
    }


def impact() -> dict[str, object]:
    return {
        "versionBase": 1,
        "plan": {"anterior": "esencial", "nuevo": "premium"},
        "coberturas": coverages(False),
        "coberturasAgregadas": [],
        "coberturasRetiradas": ["incapacidad_total"],
        "primaAnterior": {"monto": "25000", "moneda": "COP"},
        "primaNueva": {"monto": "24700", "moneda": "COP"},
        "diferenciaMensual": {"monto": "-300", "moneda": "COP"},
        "ajusteProrrateado": {"monto": "-260", "moneda": "COP"},
        "diasRestantes": 26,
        "efectivoDesde": "2026-11-16",
        "vigencia": {"inicio": "2026-11-11", "fin": "2026-12-11", "cambia": False},
        "reglaVersion": "academica-modificacion-v1",
        "impactoHash": HASH,
    }


def state() -> dict[str, object]:
    return {
        "polizaId": str(POLICY_ID),
        "tipo": "life",
        "vigencia": {"inicio": "2026-11-11", "fin": "2026-12-11"},
        "modificable": True,
        "motivo": None,
        "versionActual": version(),
        "opciones": {
            "planes": [{"codigo": "premium", "nombre": "Premium", "factor": "1.30"}],
            "coberturasOpcionales": [
                {
                    "codigo": "incapacidad_total",
                    "nombre": "Incapacidad total y permanente",
                    "recargo": {"monto": "6000", "moneda": "COP"},
                }
            ],
        },
        "historial": [version()],
    }


def result() -> dict[str, object]:
    return {
        "endosoId": str(ENDORSEMENT_ID),
        "polizaId": str(POLICY_ID),
        "versionAnterior": 1,
        "versionNueva": 2,
        "version": version(2, "premium"),
        "impacto": impact(),
        "creadoEn": "2026-11-15T15:00:00Z",
    }
