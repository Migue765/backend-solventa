import json
from html import escape
from io import BytesIO
from uuid import UUID

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

PRIMARIO = colors.HexColor("#007ca5")


def archivo_pdf(contenido: str, documento_id: UUID, contenido_hash: str) -> bytes:
    datos = json.loads(contenido)
    normal = ParagraphStyle(
        "contenido",
        fontName="Helvetica",
        fontSize=10,
        leading=15,
        textColor=colors.HexColor("#213547"),
    )
    titulo = ParagraphStyle(
        "titulo", parent=normal, fontName="Helvetica-Bold", fontSize=19, leading=24, spaceAfter=12
    )
    seccion = ParagraphStyle(
        "seccion",
        parent=normal,
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=18,
        textColor=PRIMARIO,
        spaceBefore=16,
        spaceAfter=8,
    )
    aviso = ParagraphStyle(
        "aviso",
        parent=normal,
        fontName="Helvetica-Bold",
        backColor=colors.HexColor("#e9f5fa"),
        borderPadding=10,
        spaceAfter=16,
    )
    nota = ParagraphStyle("nota", parent=normal, fontSize=8, leading=11)
    huella = ParagraphStyle("huella", parent=normal, fontName="Courier", fontSize=8, leading=12)

    def parrafo(valor: object, estilo: ParagraphStyle = normal) -> Paragraph:
        return Paragraph(escape(str(valor)), estilo)

    prima = datos["prima"]
    prima_texto = (
        f"{prima['monto']} {prima['moneda']} ({prima['periodicidad']})"
        if prima is not None
        else "No disponible en este modelo; no se estima un importe."
    )
    filas = [
        ("Documento", documento_id),
        ("Poliza", datos["polizaId"]),
        ("Cliente", datos["clienteId"]),
        ("Suscripcion", datos["suscripcionId"]),
        ("Producto", f"{datos['producto']['nombre']} ({datos['producto']['tipo']})"),
        ("Estado registrado", datos["estado"]),
        ("Vigencia", f"{datos['vigencia']['inicio']} a {datos['vigencia']['fin']}"),
        ("Emitida en UTC", datos["emitidaEn"]),
        (
            "Version de condiciones",
            datos["version"] if datos["version"] is not None else "No aplica",
        ),
        ("Plan academico", datos["plan"] if datos["plan"] is not None else "No aplica"),
        ("Prima del modelo", prima_texto),
        ("Regla de emision", datos["reglaEmision"]),
        ("Formato canonico", datos["formatoVersion"]),
        ("SHA-256 HTML canonico", contenido_hash),
    ]
    tabla = Table(
        [
            [
                parrafo(etiqueta),
                parrafo(valor, huella if etiqueta == "SHA-256 HTML canonico" else normal),
            ]
            for etiqueta, valor in filas
        ],
        colWidths=[145, A4[0] - 96 - 145],
        hAlign="LEFT",
    )
    tabla.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef4f7")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9e3e9")),
            ]
        )
    )
    elementos = [
        parrafo("Documento de poliza SIMULADO", titulo),
        parrafo(datos["aviso"], aviso),
        Spacer(1, 6),
        tabla,
        parrafo("Coberturas del modelo", seccion),
    ]
    for cobertura in datos["coberturas"]:
        texto = (
            f"{cobertura['nombre']} ({cobertura['codigo']}): {cobertura['descripcion']} "
            f"({'opcional' if cobertura['opcional'] else 'obligatoria'}, "
            f"{'incluida' if cobertura['incluida'] else 'no incluida'})"
        )
        elementos.extend([parrafo(texto), Spacer(1, 7)])
    elementos.append(parrafo("Condiciones del modelo", seccion))
    for condicion in datos["condiciones"]:
        elementos.extend([parrafo(condicion), Spacer(1, 7)])
    elementos.extend(
        [
            Spacer(1, 6),
            parrafo(
                "Representacion PDF del documento canonico persistido. La aceptacion simulada "
                "se registra por separado y no modifica este documento. No cambia la emision, "
                "la cobertura ni el estado de pago de la poliza.",
                nota,
            ),
        ]
    )
    salida = BytesIO()
    documento = SimpleDocTemplate(
        salida,
        pagesize=A4,
        rightMargin=42,
        leftMargin=42,
        topMargin=96,
        bottomMargin=54,
        title="Solventa - Documento de poliza SIMULADO",
        author="Solventa",
        subject=(
            f"Documento {documento_id}; Poliza {datos['polizaId']}; "
            f"Cliente {datos['clienteId']}; SHA-256 HTML {contenido_hash}"
        ),
        invariant=1,
        pageCompression=1,
    )
    documento.build(elementos, onFirstPage=_pagina, onLaterPages=_pagina)
    return salida.getvalue()


def _pagina(lienzo: Canvas, documento: SimpleDocTemplate) -> None:
    ancho, alto = documento.pagesize
    lienzo.saveState()
    lienzo.setFillColor(PRIMARIO)
    lienzo.setFillAlpha(0.045)
    lienzo.translate(ancho / 2, alto / 2)
    lienzo.rotate(35)
    lienzo.setFont("Helvetica-Bold", 65)
    lienzo.drawCentredString(0, 0, "SIMULADO")
    lienzo.restoreState()
    lienzo.saveState()
    _marca(lienzo, 42, alto - 26)
    lienzo.setFillColor(PRIMARIO)
    lienzo.setFont("Helvetica-Bold", 22)
    lienzo.drawString(88, alto - 47, "Solventa")
    lienzo.setFont("Helvetica", 10)
    lienzo.drawString(88, alto - 63, "Seguros y tranquilidad")
    lienzo.setStrokeColor(PRIMARIO)
    lienzo.line(42, alto - 80, ancho - 42, alto - 80)
    lienzo.setFillColor(colors.HexColor("#526675"))
    lienzo.setFont("Helvetica", 8)
    lienzo.drawString(42, 30, "Documento SIMULADO - Sin validez legal")
    lienzo.drawRightString(ancho - 42, 30, f"Pagina {documento.page}")
    lienzo.restoreState()


def _marca(lienzo: Canvas, x: float, y: float) -> None:
    # SVG autoritativo de shared/brand/brand.component.ts, convertido a curvas PDF.
    lienzo.saveState()
    lienzo.translate(x, y)
    lienzo.scale(1.6, -1.6)
    escudo = lienzo.beginPath()
    escudo.moveTo(12, 2.25)
    escudo.lineTo(20, 5.15)
    escudo.lineTo(20, 11.35)
    escudo.curveTo(20, 16.4, 16.85, 20.1, 12, 21.75)
    escudo.curveTo(7.15, 20.1, 4, 16.4, 4, 11.35)
    escudo.lineTo(4, 5.15)
    escudo.close()
    lienzo.setFillColor(PRIMARIO)
    lienzo.drawPath(escudo, fill=1, stroke=0)
    letra = lienzo.beginPath()
    letra.moveTo(15.55, 7.25)
    letra.curveTo(14.65, 6.47, 13.43, 6.05, 12.05, 6.05)
    letra.curveTo(9.9, 6.05, 8.5, 7, 8.5, 8.4)
    letra.curveTo(8.5, 9.95, 9.9, 10.52, 12.05, 11.05)
    letra.curveTo(14.15, 11.57, 15.5, 12.2, 15.5, 13.75)
    letra.curveTo(15.5, 15.25, 14.05, 16.3, 11.85, 16.3)
    letra.curveTo(10.35, 16.3, 9, 15.85, 8, 14.95)
    lienzo.setStrokeColor(colors.white)
    lienzo.setLineWidth(2.35)
    lienzo.setLineCap(1)
    lienzo.drawPath(letra, fill=0, stroke=1)
    lienzo.restoreState()
