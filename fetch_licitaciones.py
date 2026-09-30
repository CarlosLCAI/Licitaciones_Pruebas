import requests
from lxml import etree
from pypdf import PdfReader
from io import BytesIO
import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone

NS = {
    'atom': 'http://www.w3.org/2005/Atom',
    'cbc': 'urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2',
    'cac': 'urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2',
    'cac-place-ext': 'urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2',
    'cbc-place-ext': 'urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2',
}

FEED_URL = "https://contrataciondelestado.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3.atom"
VISOR_URL = "https://carloslcai.github.io/Licitaciones_Pruebas/"
VENTANA_HORAS = 72  # cubre con margen el hueco viernes tarde -> lunes madrugada (fines de semana con poca/nula publicación)
MAX_PAGINAS = 30
FILTROS_MANIFEST_FILE = "filtros.json"
FILTROS_CARPETA_BASE = "filtros"

# Nombres de archivo estándar dentro de la carpeta de cada filtro (filtros/<id>/...).
NOMBRE_CONFIG = "config.json"
NOMBRE_ESTADO = "estado.json"
NOMBRE_HISTORICO = "historico.json"
NOMBRE_RESULTADO_HOY = "resultado_hoy.json"
NOMBRE_ULTIMA_LECTURA = "ultima_lectura.json"

# Se usa solo si la carpeta del filtro "1" no tiene config.json todavía (primera ejecución).
# A partir de ahí, filtros/<id>/config.json es la fuente de verdad y se edita desde el visor.
FILTRO_CONFIG_POR_DEFECTO = {
    "nombre": "Diseño Urbano Andalucía",
    "nuts_prefix": "ES61",
    "region": "Andalucía",
    "cpv_permitidos": ["71400000", "71410000", "71420000", "71222000", "71222100", "71222200", "71240000", "71241000", "71243000", "71245000", "71510000", "90712100"],
    "estados_permitidos": ["PUB"],
    "tipos_contrato_permitidos": [],   # códigos de ProcurementProject/TypeCode; vacío = todos
    "procedimientos_permitidos": [],   # códigos de TenderingProcess/ProcedureCode; vacío = todos
    "importe_min": None,
    "importe_max": None,
}

# Config por defecto para un filtro nuevo añadido al manifiesto que aún no tiene archivo propio:
# sin ningún criterio activo (coincide con todo) hasta que se configure desde el visor.
FILTRO_CONFIG_VACIO = {
    "nombre": "Nuevo filtro",
    "nuts_prefix": "",
    "region": "",
    "cpv_permitidos": [],
    "estados_permitidos": [],
    "tipos_contrato_permitidos": [],
    "procedimientos_permitidos": [],
    "importe_min": None,
    "importe_max": None,
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/atom+xml, application/xml, text/xml, */*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Referer": "https://contrataciondelestado.es/",
}

# Revisión de solvencia por IA (opcional): se activa solo si las variables de entorno
# SOLVENCIA_EMPRESA y ANTHROPIC_API_KEY están configuradas (secrets de GitHub Actions).
# Usa la API directa de Anthropic (Claude), mandando los PDF completos como documentos
# (no texto extraído) para que Claude pueda leer visualmente páginas escaneadas sin capa
# de texto — pypdf se quedaría en blanco con esos documentos.
ANTHROPIC_API_ENDPOINT = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
# Modelo barato usado solo para localizar en qué páginas del PCAP está cada apartado, antes de
# mandarle el documento completo al modelo caro — ver localizar_paginas_relevantes().
ANTHROPIC_MODEL_LOCALIZADOR = os.environ.get("ANTHROPIC_MODEL_LOCALIZADOR", "claude-haiku-4-5-20251001")
ANTHROPIC_VERSION = "2023-06-01"

PROMPT_SISTEMA_INFORME = (
    "Eres un asistente técnico especializado en licitaciones públicas españolas de obra civil, "
    "edificación e infraestructuras, para el equipo de CAI Consultores (arquitectura/ingeniería). "
    "Analizas el PCAP (pliego de cláusulas administrativas particulares) de una licitación y "
    "extraes la información clave en un informe estructurado en 6 bloques, en este orden. Genera "
    "los 6 bloques completos en una sola respuesta: esto se ejecuta de forma automática, sin "
    "nadie revisando ni validando entre bloques.\n\n"
    "El texto del PCAP que recibes puede venir recortado a las páginas más relevantes (verás "
    "marcas \"--- Página N ---\"); si no encuentras algo, no asumas que no existe en el documento "
    "completo — indícalo como no localizado en vez de darlo por ausente.\n\n"
    "FORMATO: responde en Markdown. Usa exactamente '## Bloque N – Título' para cada uno de los "
    "6 títulos de bloque, y '### ' para los subtítulos interiores de cada bloque. Usa **negrita** "
    "para resaltar cifras y datos clave, viñetas '- ' o listas numeradas '1. ' donde corresponda, "
    "y tablas Markdown (fila de cabecera + fila separadora '---') para cualquier dato tabular "
    "(presupuestos por lote, baremos de puntuación, etc.). No uses HTML ni describas colores o "
    "tipografías — el formato visual lo aplica el visor, tú solo estructuras el contenido.\n\n"
    "## Bloque 1 – Características Principales del Contrato\n"
    "### Objeto de la licitación\n"
    "Descripción precisa (50-70 palabras) y lista de objetivos.\n"
    "### División en lotes\n"
    "Lista numerada de lotes, o indica que no hay división en lotes.\n"
    "### Características del contrato\n"
    "Presupuesto base (por lote si aplica), duración, plazo/lugar/forma de presentación de "
    "ofertas, y trabajos específicos obligatorios (arqueología, topografía, geotecnia, trámites "
    "ambientales…).\n\n"
    "## Bloque 2 – Solvencia\n"
    "### Económica y financiera\n"
    "Solvencia económica mínima exigida, cifra de negocio solicitada, y seguros u otra "
    "acreditación requerida.\n"
    "### Técnica y profesional\n"
    "Titulación exigida por perfil, experiencia mínima y su forma de acreditación, y proyectos de "
    "referencia exigidos (tipología, importe). Indica el equipo mínimo por lote y qué puestos "
    "pueden subcontratarse. Si existe un criterio de mejora por experiencia adicional del equipo "
    "distinto del mínimo de solvencia, márcalo aparte con \"⚠ REVISAR — criterio de mejora de "
    "equipo, no confundir con el mínimo de solvencia\".\n\n"
    "## Bloque 3 – Criterios de Adjudicación\n"
    "### Automáticos (fórmulas)\n"
    "Detalla la fórmula económica y cualquier otro criterio automático.\n"
    "### Juicio de valor\n"
    "Desglose y subapartados de metodología, con su puntuación.\n"
    "### Umbrales mínimos de puntuación\n"
    "Indica si existen, o que no los hay.\n\n"
    "## Bloque 4 – Forma de Pago\n"
    "Valoración de trabajos, certificaciones y facturación, y condiciones adicionales "
    "(penalizaciones, revisión de precios, etc.).\n\n"
    "## Bloque 5 – Documentación a Presentar\n"
    "Desglosa en lista el número de sobres y, dentro de cada uno ('### Sobre 1', '### Sobre 2', "
    "etc.), qué información debe presentarse (también en lista). Si el PCAP no detalla esto, "
    "indícalo como no localizado en vez de inventarlo.\n\n"
    "## Bloque 6 – Conclusiones / Análisis Preliminar\n"
    "Analiza el precio de licitación en relación con la duración del contrato. Compara los "
    "requisitos de solvencia económica y técnica de los Bloques 2 y 3 contra el perfil de la "
    "empresa que se te facilita en el mensaje (equipo, experiencia, cifra de negocio), y valora "
    "si CAI Consultores cumple los mínimos o qué le faltaría acreditar.\n\n"
    "Reglas generales: responde en español técnico, basándote solo en el PCAP proporcionado "
    "(nunca extrapoles de otros pliegos), sé riguroso con baremos y limitaciones de formato, y "
    "ante cualquier duda o incoherencia en el documento, no la inventes — indícalo y sugiere "
    "revisar ese punto concreto del PCAP."
)

# --- Localización de secciones relevantes (paso barato con Haiku) ------------------------------
# En vez de mandar el PCAP entero al modelo caro, primero se le pasa a un modelo barato un
# resumen (solo el inicio de cada página) para que diga en qué páginas está cada apartado del
# informe. Con eso se construye un documento reducido con solo esas páginas (+ margen), que es
# lo que de verdad se manda a generar el informe. Si esta llamada falla por lo que sea, se cae
# al recorte por Anexo I/III de siempre (ver recortar_preservando_anexos).

PROMPT_SISTEMA_LOCALIZADOR = (
    "Eres un asistente que localiza en qué páginas de un PCAP (pliego de cláusulas "
    "administrativas particulares) de una licitación pública española aparece cada uno de los "
    "apartados indicados en la herramienta. Se te da, página por página, solo el inicio del "
    "texto de cada una, no el documento completo. Devuelve los números de página (empezando en "
    "1) donde aparece cada apartado; una misma página puede repetirse en varios apartados, y "
    "deja la lista vacía si no la localizas. No inventes páginas que no veas en el texto "
    "proporcionado."
)

HERRAMIENTA_LOCALIZADOR = {
    "name": "localizar_secciones",
    "description": "Registra en qué páginas del PCAP aparece cada apartado del informe.",
    "input_schema": {
        "type": "object",
        "properties": {
            "objeto_y_caracteristicas": {
                "type": "array", "items": {"type": "integer"},
                "description": "Objeto, lotes, presupuesto, duración, plazos y trabajos "
                               "específicos obligatorios (arqueología, topografía, geotecnia, "
                               "trámites ambientales…).",
            },
            "solvencia": {
                "type": "array", "items": {"type": "integer"},
                "description": "Solvencia económica/financiera y técnica/profesional, equipo "
                               "mínimo, titulaciones y experiencia exigida.",
            },
            "criterios_adjudicacion": {
                "type": "array", "items": {"type": "integer"},
                "description": "Criterios de adjudicación automáticos (fórmulas) y de juicio de "
                               "valor.",
            },
            "forma_pago": {
                "type": "array", "items": {"type": "integer"},
                "description": "Forma de pago, valoración de trabajos, certificaciones, "
                               "facturación, penalizaciones, revisión de precios.",
            },
            "documentacion_a_presentar": {
                "type": "array", "items": {"type": "integer"},
                "description": "Sobres y documentación que hay que presentar para licitar.",
            },
        },
        "required": [
            "objeto_y_caracteristicas", "solvencia", "criterios_adjudicacion", "forma_pago",
            "documentacion_a_presentar",
        ],
    },
}


def localizar_paginas_relevantes(paginas_texto, api_key):
    """Llamada barata (Haiku) que decide qué páginas hacen falta leer para cada apartado del
    informe, a partir de un resumen de cada página (no el documento entero). Lanza una excepción
    si algo falla; el llamador cae entonces al recorte por Anexo I/III de siempre."""
    indice = "\n".join(
        f"[Página {i + 1}] {(texto or '').strip()[:220]}"
        for i, texto in enumerate(paginas_texto)
    )
    indice = indice[:60000]  # tope de seguridad para documentos con muchísimas páginas

    payload = {
        "model": ANTHROPIC_MODEL_LOCALIZADOR,
        "max_tokens": 1024,
        # Sin cache_control aquí: Haiku 4.5 exige 4.096 tokens mínimo para cachear, y este
        # prompt del sistema (~130 tokens) más la herramienta no se acercan a eso — cachearlo
        # no haría nada (la API lo ignora sin más), así que no lo declaramos.
        "system": PROMPT_SISTEMA_LOCALIZADOR,
        "tools": [HERRAMIENTA_LOCALIZADOR],
        "tool_choice": {"type": "tool", "name": "localizar_secciones"},
        "messages": [{
            "role": "user",
            "content": f"Documento de {len(paginas_texto)} páginas:\n\n{indice}",
        }],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "Content-Type": "application/json",
    }
    resp = requests.post(ANTHROPIC_API_ENDPOINT, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    bloque = next(b for b in data["content"] if b.get("type") == "tool_use")
    return bloque["input"]


def construir_texto_reducido(paginas_texto, secciones, margen=1, max_chars=200000):
    """A partir de las páginas que ha localizado la IA (+ 1 página de margen a cada lado),
    construye el texto reducido que se manda a generar el informe. Siempre incluye las primeras
    páginas (portada/índice/objeto) y cualquier página con Anexo I/III, aunque el localizador no
    las haya marcado, por si el localizador se equivoca."""
    total_paginas = len(paginas_texto)
    incluidas = set(range(1, min(3, total_paginas) + 1))

    for paginas in secciones.values():
        for p in paginas:
            for pp in range(p - margen, p + margen + 1):
                if 1 <= pp <= total_paginas:
                    incluidas.add(pp)

    for i, texto in enumerate(paginas_texto):
        if any(re.search(patron, (texto or "").lower()) for patron in PATRONES_ANEXO):
            for pp in range(i + 1 - margen, i + 1 + margen + 1):
                if 1 <= pp <= total_paginas:
                    incluidas.add(pp)

    paginas_ordenadas = sorted(incluidas)
    texto = "\n\n".join(f"--- Página {p} ---\n{paginas_texto[p - 1]}" for p in paginas_ordenadas)
    if len(texto) > max_chars:
        texto = texto[:max_chars]
    return texto, paginas_ordenadas


# Patrones para localizar el Anexo I / Anexo III dentro del texto del PCAP — es donde suelen
# vivir los requisitos concretos de solvencia económica/técnica cuando el cuerpo de cláusulas
# se limita a remitir a ellos. \b evita que "anexo i" case dentro de "anexo iii".
PATRONES_ANEXO = [r'anexo\s+i\b', r'anexo\s+1\b', r'anexo\s+iii\b', r'anexo\s+3\b']


def recortar_preservando_anexos(texto, max_chars):
    """Si el texto cabe entero, no se toca. Si hay que recortar, en vez de cortar a ciegas
    por los primeros caracteres (arriesgándose a perder el Anexo I/III, que suele estar pasada
    la página 50), se localiza dónde empieza y se preserva ese bloque completo, recortando de
    en medio si hace falta."""
    if len(texto) <= max_chars:
        return texto

    texto_lower = texto.lower()
    posiciones = [m.start() for patron in PATRONES_ANEXO for m in re.finditer(patron, texto_lower)]
    if not posiciones:
        return texto[:max_chars]

    inicio_anexo = min(posiciones)
    presupuesto_inicio = max_chars // 3
    inicio_doc = texto[:presupuesto_inicio]
    separador = "\n[...]\n"
    espacio_restante = max(max_chars - len(inicio_doc) - len(separador), 0)
    return inicio_doc + separador + texto[inicio_anexo:inicio_anexo + espacio_restante]


def obtener_pcap(url, api_key=None, min_chars_por_pagina=100, max_chars_texto=350000, max_bytes_documento=32 * 1024 * 1024):
    """Descarga el PCAP y decide cómo se mandará a la IA:
    - Si tiene una capa de texto razonable (documento generado digitalmente, el caso normal), se
      extrae el texto con pypdf — mucho más barato en tokens que mandar el PDF entero (Claude
      convierte cada página de un "documento" en una imagen, ~1.500-3.000+ tokens por página).
      Con la clave de la API disponible, primero se localizan (con Haiku, barato) las páginas
      relevantes para cada apartado del informe y solo esas se mandan al modelo caro — un PCAP de
      50+ páginas puede quedar reducido a un puñado de páginas reales. Si la localización falla
      por lo que sea, se cae al recorte por Anexo I/III de siempre (350.000 caracteres de tope).
    - Si el texto extraído es casi nulo (indicio de páginas escaneadas/rasterizadas), se manda
      el PDF completo en base64 como documento, para que Claude lo lea visualmente — esto sí es
      caro, pero solo ocurre cuando de verdad hace falta OCR.
    Devuelve (tipo, contenido) con tipo en {"texto", "documento", None}.
    """
    if not url:
        return None, None
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    contenido_bytes = resp.content

    try:
        lector = PdfReader(BytesIO(contenido_bytes))
        paginas_texto = [pagina.extract_text() or "" for pagina in lector.pages]
        num_paginas = len(paginas_texto)
        texto_completo = "\n".join(paginas_texto)
    except Exception:
        num_paginas = 1
        paginas_texto = []
        texto_completo = ""

    promedio_por_pagina = len(texto_completo) / max(num_paginas, 1)
    if promedio_por_pagina >= min_chars_por_pagina:
        if api_key and num_paginas > 1:
            try:
                secciones = localizar_paginas_relevantes(paginas_texto, api_key)
                texto_reducido, paginas_incluidas = construir_texto_reducido(
                    paginas_texto, secciones, max_chars=max_chars_texto
                )
                print(
                    f"    Localizador: usando {len(paginas_incluidas)}/{num_paginas} páginas "
                    f"({len(texto_reducido)} caracteres) en vez del documento completo."
                )
                return "texto", texto_reducido
            except Exception as e:
                print(f"    Aviso: falló la localización de secciones ({e}); se usa el recorte por Anexo I/III de siempre.")
        if len(texto_completo) > max_chars_texto:
            print(f"    Aviso: PCAP de {len(texto_completo)} caracteres supera el tope de {max_chars_texto}; se recorta preservando el Anexo I/III si se localiza.")
        return "texto", recortar_preservando_anexos(texto_completo, max_chars_texto)

    if len(contenido_bytes) > max_bytes_documento:
        print(f"    Aviso: PCAP de {len(contenido_bytes)} bytes supera el máximo de {max_bytes_documento}; se omite.")
        return None, None
    return "documento", base64.b64encode(contenido_bytes).decode("ascii")


def generar_informe_licitacion_ia(perfil_empresa, pcap_tipo, pcap_contenido, titulo, organo, api_key):
    if not api_key:
        return {"informe": "No hay ANTHROPIC_API_KEY configurada para llamar a Claude."}
    if not pcap_tipo:
        return {"informe": "No se pudo descargar el PCAP."}

    fecha_hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # El perfil de empresa es idéntico en todas las licitaciones de una misma ejecución: se manda
    # como bloque cacheable aparte (cache_control) para que, a partir de la segunda licitación
    # analizada en el mismo día, ese bloque (y el prompt del sistema) salgan con el descuento de
    # caché de Anthropic en vez de pagarse entero en cada llamada.
    bloque_perfil = {
        "type": "text",
        "text": f"PERFIL DE LA EMPRESA (CAI Consultores):\n{perfil_empresa}",
        "cache_control": {"type": "ephemeral"},
    }
    texto_variable = (
        f"FECHA DE HOY: {fecha_hoy}\n"
        f"LICITACIÓN: {titulo}\n"
        f"ÓRGANO: {organo}\n\n"
        f"A continuación se adjunta el PCAP (o el fragmento relevante ya localizado) de esta "
        f"licitación. Genera el informe según las instrucciones del sistema."
    )

    if pcap_tipo == "documento":
        bloque_pcap = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": pcap_contenido},
        }
    else:
        bloque_pcap = {"type": "text", "text": f"TEXTO DEL PCAP:\n{pcap_contenido}"}

    contenido_mensaje = [bloque_perfil, {"type": "text", "text": texto_variable}, bloque_pcap]

    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 4096,
        "system": [{
            "type": "text", "text": PROMPT_SISTEMA_INFORME,
            "cache_control": {"type": "ephemeral"},
        }],
        "messages": [{"role": "user", "content": contenido_mensaje}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "Content-Type": "application/json",
    }

    # Diagnóstico de tamaños (nunca se imprime el contenido del perfil, es un secret).
    print(
        f"    Tamaños enviados a la IA -> perfil: {len(perfil_empresa)} car., "
        f"PCAP: modo '{pcap_tipo}', {len(pcap_contenido)} caracteres"
    )

    try:
        resp = requests.post(ANTHROPIC_API_ENDPOINT, headers=headers, json=payload, timeout=180)
        if not resp.ok:
            print(f"    Respuesta de error de Anthropic (HTTP {resp.status_code}): {resp.text[:2000]}")
        resp.raise_for_status()
        data = resp.json()
        bloque_texto = next(b["text"] for b in data["content"] if b.get("type") == "text")
        uso = data.get("usage", {})
        print(
            f"    Tokens usados -> entrada: {uso.get('input_tokens')}, salida: {uso.get('output_tokens')}, "
            f"caché escrita: {uso.get('cache_creation_input_tokens')}, caché leída: {uso.get('cache_read_input_tokens')}"
        )
        return {"informe": bloque_texto.strip()}
    except Exception as e:
        return {"informe": f"Error al generar el informe con IA: {e}"}


def analizar_licitacion_ia(r, perfil_empresa):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        pcap_tipo, pcap_contenido = obtener_pcap(r.get("pcap_url"), api_key=api_key)
    except Exception:
        pcap_tipo, pcap_contenido = None, None

    resultado = generar_informe_licitacion_ia(
        perfil_empresa, pcap_tipo, pcap_contenido, r.get("titulo"), r.get("organo"), api_key
    )
    resultado["fecha_analisis"] = datetime.now(timezone.utc).isoformat()
    return resultado


def cargar_manifiesto_filtros():
    if os.path.exists(FILTROS_MANIFEST_FILE):
        with open(FILTROS_MANIFEST_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("filtros", [])
    # Primera vez que se ejecuta con soporte multi-filtro: se crea el manifiesto
    # con un único filtro "1" en filtros/1/.
    manifiesto = {"filtros": [{"id": "1", "carpeta": f"{FILTROS_CARPETA_BASE}/1"}]}
    with open(FILTROS_MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifiesto, f, ensure_ascii=False, indent=2)
    return manifiesto["filtros"]


def ruta_filtro(desc, nombre_archivo):
    carpeta = desc["carpeta"]
    os.makedirs(carpeta, exist_ok=True)
    return os.path.join(carpeta, nombre_archivo)


def cargar_estado(archivo):
    if os.path.exists(archivo):
        with open(archivo, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"ids_vistos": []}


def guardar_estado(archivo, estado):
    with open(archivo, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


def cargar_filtro_config(archivo, config_por_defecto):
    if os.path.exists(archivo):
        with open(archivo, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return {**config_por_defecto, **cfg}
    with open(archivo, "w", encoding="utf-8") as f:
        json.dump(config_por_defecto, f, ensure_ascii=False, indent=2)
    return dict(config_por_defecto)


def parse_entry(entry, filtro_cfg):
    def find_text(path):
        el = entry.find(path, NS)
        return el.text if el is not None else None

    folder_id = find_text('.//cbc:ContractFolderID')
    estado_code = find_text('.//cbc-place-ext:ContractFolderStatusCode')
    titulo = find_text('atom:title')
    updated = find_text('atom:updated')
    summary_text = find_text('atom:summary')

    link_el = entry.find('atom:link', NS)
    link = link_el.get('href') if link_el is not None else None

    nuts_prefix = filtro_cfg.get("nuts_prefix") or ""
    nuts_codes = [e.text for e in entry.findall('.//cbc:CountrySubentityCode', NS)]
    es_andalucia = (not nuts_prefix) or any(c and c.startswith(nuts_prefix) for c in nuts_codes)

    cpv_permitidos = filtro_cfg.get("cpv_permitidos") or []
    cpv_codes = [e.text for e in entry.findall('.//cbc:ItemClassificationCode', NS)]
    cpv_match = any(c in cpv_permitidos for c in cpv_codes)

    pcap_el = entry.find('.//cac:LegalDocumentReference//cbc:URI', NS)
    pcap_url = pcap_el.text if pcap_el is not None else None

    ppt_el = entry.find('.//cac:TechnicalDocumentReference//cbc:URI', NS)
    ppt_url = ppt_el.text if ppt_el is not None else None
    
    deadline_el = entry.find('.//cac:TenderingProcess/cac:TenderSubmissionDeadlinePeriod/cbc:EndDate', NS)
    fecha_limite = deadline_el.text if deadline_el is not None else None

    organo, importe = None, None
    if summary_text:
        organo_match = re.search(r'rgano de Contrataci.n:\s*(.*?);\s*Importe', summary_text)
        importe_match = re.search(r'Importe:\s*([\d.,]+)\s*EUR', summary_text)
        organo = organo_match.group(1).strip() if organo_match else None
        importe = importe_match.group(1).strip() if importe_match else None

    tipo_contrato = find_text('.//cac:ProcurementProject/cbc:TypeCode')
    procedimiento = find_text('.//cac:TenderingProcess/cbc:ProcedureCode')

    importe_num = None
    importe_el = entry.find('.//cac:ProcurementProject/cac:BudgetAmount/cbc:EstimatedOverallContractAmount', NS)
    if importe_el is not None and importe_el.text:
        try:
            importe_num = float(importe_el.text)
        except ValueError:
            importe_num = None

    tipos_contrato_permitidos = filtro_cfg.get("tipos_contrato_permitidos") or []
    tipo_contrato_match = not tipos_contrato_permitidos or tipo_contrato in tipos_contrato_permitidos

    procedimientos_permitidos = filtro_cfg.get("procedimientos_permitidos") or []
    procedimiento_match = not procedimientos_permitidos or procedimiento in procedimientos_permitidos

    importe_min = filtro_cfg.get("importe_min")
    importe_max = filtro_cfg.get("importe_max")
    importe_match = True
    if importe_num is not None:
        if importe_min is not None and importe_num < importe_min:
            importe_match = False
        if importe_max is not None and importe_num > importe_max:
            importe_match = False

    return {
        "folder_id": folder_id,
        "estado": estado_code,
        "titulo": titulo,
        "updated": updated,
        "link": link,
        "es_andalucia": es_andalucia,
        "cpv_match": cpv_match,
        "cpv_codes": cpv_codes,
        "pcap_url": pcap_url,
        "ppt_url": ppt_url,
        "organo": organo,
        "importe": importe,
        "importe_num": importe_num,
        "fecha_limite": fecha_limite,
        "tipo_contrato": tipo_contrato,
        "tipo_contrato_match": tipo_contrato_match,
        "procedimiento": procedimiento,
        "procedimiento_match": procedimiento_match,
        "importe_match": importe_match,
    }


import time

def fetch_pagina(url, intentos=3):
    for intento in range(1, intentos + 1):
        resp = None
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return etree.fromstring(resp.content)
        except (requests.exceptions.RequestException, etree.XMLSyntaxError) as e:
            print(f"Intento {intento}/{intentos} fallido para {url}: {e}")
            if resp is not None:
                print(f"    HTTP {resp.status_code} | primeros 300 caracteres de la respuesta: {resp.text[:300]!r}")
            if intento == intentos:
                raise
            time.sleep(5 * intento)  # espera creciente: 5s, 10s, 15s...


def get_next_link(root):
    next_el = root.find('atom:link[@rel="next"]', NS)
    return next_el.get('href') if next_el is not None else None
    
def notificar_teams(resultados, paginas, total_entries, nombre_filtro="Monitor"):
    webhook_url = os.environ.get("TEAMS_WEBHOOK_URL")
    if not webhook_url:
        print("Aviso: no se ha configurado TEAMS_WEBHOOK_URL, se omite notificación.")
        return

    ahora = datetime.now(timezone.utc).strftime("%H:%M UTC")

    texto_resumen = (
        f"**Lectura PLACSP — {nombre_filtro}** ({ahora})\n\n"
        f"Páginas leídas: {paginas}  \n"
        f"Entries totales leídas: {total_entries}  \n"
        f"Licitaciones nuevas filtradas: {len(resultados)}\n\n"
        f"[Abrir visor completo]({VISOR_URL})"
    )

    if resultados:
        detalle = "\n\n".join(
            f"- **[{r['folder_id']}]** {r['titulo']}  \n{r['link']}"
            for r in resultados[:15]
        )
        texto_resumen += f"\n\n{detalle}"
        if len(resultados) > 15:
            texto_resumen += f"\n\n_(y {len(resultados) - 15} más)_"

    adaptive_card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": texto_resumen,
                "wrap": True
            }
        ],
        "actions": [
            {
                "type": "Action.OpenUrl",
                "title": "Abrir visor",
                "url": VISOR_URL
            }
        ]
    }

    payload = {
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": adaptive_card
            }
        ]
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error notificando a Teams: {e}")


def main():
    filtros_desc = cargar_manifiesto_filtros()

    contextos = []
    for desc in filtros_desc:
        config_por_defecto = FILTRO_CONFIG_POR_DEFECTO if desc["id"] == "1" else FILTRO_CONFIG_VACIO
        cfg = cargar_filtro_config(ruta_filtro(desc, NOMBRE_CONFIG), config_por_defecto)
        estado = cargar_estado(ruta_filtro(desc, NOMBRE_ESTADO))
        contextos.append({
            "desc": desc,
            "cfg": cfg,
            "ids_vistos": set(estado.get("ids_vistos", [])),
            "total_entries_acumulado_previo": estado.get("total_entries_acumulado", 0),
            "resultados": [],
        })

    limite_fecha = datetime.now(timezone.utc) - timedelta(hours=VENTANA_HORAS)
    url_actual = FEED_URL
    pagina = 0
    total_entries_leidas = 0

    while url_actual and pagina < MAX_PAGINAS:
        pagina += 1
        root = fetch_pagina(url_actual)
        entries = root.findall('atom:entry', NS)

        if not entries:
            break

        parar = False
        for entry in entries:
            total_entries_leidas += 1
            updated_el = entry.find('atom:updated', NS)
            if updated_el is None or not updated_el.text:
                continue
            fecha_entry = datetime.fromisoformat(updated_el.text)
            if fecha_entry < limite_fecha:
                parar = True
                break

            for ctx in contextos:
                cfg = ctx["cfg"]
                data = parse_entry(entry, cfg)
                cpv_permitidos = cfg.get("cpv_permitidos") or []
                estados_permitidos = cfg.get("estados_permitidos") or []

                if (data["es_andalucia"]
                    and (not cpv_permitidos or data["cpv_match"])
                    and (not estados_permitidos or data["estado"] in estados_permitidos)
                    and data["tipo_contrato_match"]
                    and data["procedimiento_match"]
                    and data["importe_match"]
                    and data["folder_id"] not in ctx["ids_vistos"]):
                    ctx["resultados"].append(data)
                    ctx["ids_vistos"].add(data["folder_id"])

        if parar:
            break

        url_actual = get_next_link(root)
        if url_actual:
            time.sleep(2)  # pausa entre páginas para no parecer scraping automático agresivo

    print(f"Páginas leídas: {pagina}")
    print(f"Licitaciones leídas (total entries): {total_entries_leidas}")

    fecha_hora_iso = datetime.now(timezone.utc).isoformat()
    fecha_captura_hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    perfil_empresa = os.environ.get("SOLVENCIA_EMPRESA")

    resultados_por_filtro = {}
    for ctx in contextos:
        desc = ctx["desc"]
        resultados = ctx["resultados"]
        nombre_filtro = ctx["cfg"].get("nombre") or f"Filtro {desc['id']}"
        print(f"[{nombre_filtro}] licitaciones nuevas filtradas: {len(resultados)}")

        if perfil_empresa:
            for r in resultados:
                r["revision_ia"] = analizar_licitacion_ia(r, perfil_empresa)
                print(f"    -> Informe IA generado para [{r['folder_id']}] ({len(r['revision_ia']['informe'])} caracteres)")

        total_entries_acumulado = ctx["total_entries_acumulado_previo"] + total_entries_leidas
        guardar_estado(ruta_filtro(desc, NOMBRE_ESTADO), {
            "ids_vistos": list(ctx["ids_vistos"]),
            "total_entries_acumulado": total_entries_acumulado,
        })

        metadata_lectura = {
            "fecha_hora": fecha_hora_iso,
            "paginas": pagina,
            "total_entries_leidas": total_entries_leidas,
            "nuevas_filtradas": len(resultados),
            "total_entries_acumulado": total_entries_acumulado,
        }
        with open(ruta_filtro(desc, NOMBRE_ULTIMA_LECTURA), "w", encoding="utf-8") as f:
            json.dump(metadata_lectura, f, ensure_ascii=False, indent=2)

        for r in resultados:
            print(f"  - [{r['folder_id']}] {r['titulo']} | {r['link']}")

        with open(ruta_filtro(desc, NOMBRE_RESULTADO_HOY), "w", encoding="utf-8") as f:
            json.dump(resultados, f, ensure_ascii=False, indent=2)

        for r in resultados:
            r["fecha_captura"] = fecha_captura_hoy

        archivo_historico = ruta_filtro(desc, NOMBRE_HISTORICO)
        if os.path.exists(archivo_historico):
            with open(archivo_historico, "r", encoding="utf-8") as f:
                try:
                    historico = json.load(f)
                except json.JSONDecodeError as e:
                    raise RuntimeError(
                        f"{archivo_historico} tiene un error de sintaxis JSON ({e}). "
                        "No se puede continuar sin arreglarlo a mano (revisa comas sobrantes o "
                        "corchetes/llaves sin cerrar) para no arriesgarse a perder el histórico."
                    ) from e
        else:
            historico = []
        historico.extend(resultados)
        with open(archivo_historico, "w", encoding="utf-8") as f:
            json.dump(historico, f, ensure_ascii=False, indent=2)

        notificar_teams(resultados, pagina, total_entries_leidas, nombre_filtro)
        resultados_por_filtro[desc["id"]] = resultados

    return resultados_por_filtro


def notificar_fallo_teams(mensaje_error):
    webhook_url = os.environ.get("TEAMS_WEBHOOK_URL")
    if not webhook_url:
        return

    ahora = datetime.now(timezone.utc).strftime("%H:%M UTC")
    texto = (
        f"**⚠ Lectura PLACSP fallida** ({ahora})\n\n"
        f"La ejecución de hoy no ha podido completarse:\n\n"
        f"`{mensaje_error}`\n\n"
        f"No se han detectado licitaciones nuevas en esta ejecución. "
        f"Se reintentará en la próxima ejecución programada."
    )
    adaptive_card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": [{"type": "TextBlock", "text": texto, "wrap": True}],
    }
    payload = {
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": adaptive_card}
        ]
    }
    try:
        requests.post(webhook_url, json=payload, timeout=15).raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error notificando el fallo a Teams: {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fallo la ejecucion: {e}")
        notificar_fallo_teams(str(e))
        raise
