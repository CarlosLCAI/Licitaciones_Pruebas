import requests
from lxml import etree
from pypdf import PdfReader
from io import BytesIO
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
VENTANA_HORAS = 30
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

# Revisión de solvencia por IA (opcional): se activa solo si la variable de entorno
# SOLVENCIA_EMPRESA está configurada (secret de GitHub Actions). Usa GitHub Models,
# autenticado con el propio GITHUB_TOKEN del workflow — sin API key ni facturación aparte.
GITHUB_MODELS_ENDPOINT = "https://models.github.ai/inference/chat/completions"
GITHUB_MODELS_MODEL = os.environ.get("GITHUB_MODELS_MODEL", "openai/gpt-4.1")
CLASIFICACIONES_VALIDAS = {"Apto", "Apto / UTE", "No Apto", "Revisión"}

PROMPT_SISTEMA_SOLVENCIA = (
    "Eres un asistente que evalúa si una empresa de arquitectura/ingeniería puede presentarse a "
    "una licitación pública española, comparando el perfil de la empresa contra el PCAP y el PPT "
    "de esa licitación. Razona paso a paso internamente antes de responder, pero en la salida "
    "final solo debes dar el JSON pedido — no muestres tu razonamiento intermedio.\n\n"
    "Debes revisar tres cosas, en este orden:\n\n"
    "1. COMPOSICIÓN DEL EQUIPO (normalmente en el PPT, apartado de equipo mínimo o medios "
    "personales): compara los roles y titulaciones exigidas contra el \"Equipo técnico disponible\" "
    "del perfil de la empresa. No te limites a un cruce literal de nombres de titulación: razona si "
    "un rol exigido podría cubrirlo razonablemente alguien del equipo con una titulación distinta a "
    "la literal, teniendo en cuenta las atribuciones profesionales reales en España (por ejemplo, un "
    "Arquitecto Técnico/Aparejador puede asumir dirección de ejecución material pero no la autoría "
    "de un proyecto que exige Arquitecto superior si así lo fija la LOE; un Ingeniero de Caminos "
    "puede cubrir roles de obra civil/hidráulica pero no firmar un proyecto de edificación que "
    "requiera visado de Arquitecto, salvo que el PPT lo permita explícitamente). Si tienes dudas "
    "razonables sobre si una titulación distinta es legalmente válida para un rol, sé conservador y "
    "indica ese punto en el motivo en vez de asumir que sí vale.\n"
    "2. SOLVENCIA ECONÓMICA Y FINANCIERA (en el PCAP, normalmente un volumen de negocios mínimo "
    "anual o acumulado): compara contra el volumen de negocios del perfil.\n"
    "3. SOLVENCIA TÉCNICA Y PROFESIONAL (en el PCAP, normalmente trabajos similares ejecutados en "
    "los últimos años, y a veces clasificación empresarial): compara contra las obras/servicios "
    "similares del perfil.\n\n"
    "Además, si el PCAP incluye criterios de adjudicación cuantificables automáticamente o mediante "
    "fórmulas matemáticas relacionados con experiencia complementaria del equipo, mejoras ofertadas "
    "o experiencia en trabajos previos, razona con detalle en el campo "
    "\"valoracion_criterios_adjudicacion\" cómo de bien posicionado parece el perfil de la empresa en "
    "cada uno de esos criterios (por qué, con qué puntuación relativa podría competir, qué le falta "
    "para maximizar la puntuación) — esto NO debe cambiar la clasificación de aptitud, es información "
    "adicional para preparar la oferta.\n\n"
    "Responde ÚNICAMENTE con un JSON de la forma "
    '{"clasificacion": "...", "motivo": "...", "valoracion_criterios_adjudicacion": "..."}, '
    "sin texto adicional ni bloques de código.\n\n"
    "Valores posibles de \"clasificacion\" (exactamente uno de estos, tal cual):\n"
    "- \"Apto\": la empresa cumple los tres puntos (equipo, solvencia económica, solvencia técnica) "
    "en solitario.\n"
    "- \"Apto / UTE\": no cumple en solitario, pero sí lo haría formando una UTE con sus socios "
    "habituales según su perfil (p. ej. sumando volumen de negocios o completando roles del equipo).\n"
    "- \"No Apto\": no cumple los requisitos ni en solitario ni en UTE.\n"
    "- \"Revisión\": no hay información suficiente en el PCAP/PPT o en el perfil para decidir con "
    "confianza, o los documentos no se han podido leer.\n\n"
    "El campo \"motivo\" debe ser una frase breve (máximo 50 palabras): qué punto de los tres falla o "
    "cumple, incluyendo si alguna sustitución de titulación fue determinante.\n"
    "El campo \"valoracion_criterios_adjudicacion\" puede ser más largo (hasta 120 palabras) y debe "
    "razonar sobre los criterios de adjudicación cuantificables si existen; si el PCAP no tiene "
    "criterios de este tipo o no se pudieron leer, indícalo brevemente ahí."
)


def extraer_texto_pdf(url, max_chars=10000):
    if not url:
        return ""
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    lector = PdfReader(BytesIO(resp.content))
    fragmentos = []
    total = 0
    for pagina in lector.pages:
        texto_pagina = pagina.extract_text() or ""
        fragmentos.append(texto_pagina)
        total += len(texto_pagina)
        if total >= max_chars:
            break
    return "\n".join(fragmentos)[:max_chars]


def clasificar_solvencia_ia(perfil_empresa, texto_pcap, texto_ppt, titulo, organo):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return {
            "clasificacion": "Revisión",
            "motivo": "No hay GITHUB_TOKEN configurado para llamar a GitHub Models.",
            "valoracion_criterios_adjudicacion": "",
        }
    if not texto_pcap and not texto_ppt:
        return {
            "clasificacion": "Revisión",
            "motivo": "No se pudo obtener texto del PCAP ni del PPT.",
            "valoracion_criterios_adjudicacion": "",
        }

    # Tope de seguridad sobre el perfil de la empresa: no controlamos su tamaño (viene de un
    # secret editado a mano) y un perfil demasiado largo podría hacer que la petición a la IA
    # supere el límite de tamaño de GitHub Models (HTTP 413).
    PERFIL_MAX_CHARS = 6000
    if len(perfil_empresa) > PERFIL_MAX_CHARS:
        print(f"    Aviso: perfil de solvencia truncado de {len(perfil_empresa)} a {PERFIL_MAX_CHARS} caracteres.")
        perfil_empresa = perfil_empresa[:PERFIL_MAX_CHARS]

    fecha_hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    mensaje_usuario = (
        f"FECHA DE HOY: {fecha_hoy} (usa esta fecha para calcular años de experiencia si el perfil "
        f"indica un año de titulación en vez de un número de años ya calculado)\n"
        f"LICITACIÓN: {titulo}\n"
        f"ÓRGANO: {organo}\n\n"
        f"PERFIL DE SOLVENCIA DE LA EMPRESA:\n{perfil_empresa}\n\n"
        f"EXTRACTO DEL PCAP (requisitos de solvencia y otros):\n{texto_pcap or '(no disponible)'}\n\n"
        f"EXTRACTO DEL PPT (objeto y alcance técnico):\n{texto_ppt or '(no disponible)'}"
    )

    payload = {
        "model": GITHUB_MODELS_MODEL,
        "messages": [
            {"role": "system", "content": PROMPT_SISTEMA_SOLVENCIA},
            {"role": "user", "content": mensaje_usuario},
        ],
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Diagnóstico de tamaños (nunca se imprime el contenido del perfil, es un secret).
    payload_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    print(
        f"    Tamaños enviados a la IA -> perfil: {len(perfil_empresa)} car., "
        f"PCAP: {len(texto_pcap or '')} car., PPT: {len(texto_ppt or '')} car., "
        f"payload total: {payload_bytes} bytes"
    )

    try:
        resp = requests.post(GITHUB_MODELS_ENDPOINT, headers=headers, json=payload, timeout=60)
        if not resp.ok:
            print(f"    Respuesta de error de GitHub Models (HTTP {resp.status_code}): {resp.text[:2000]}")
        resp.raise_for_status()
        contenido = resp.json()["choices"][0]["message"]["content"].strip()
        contenido = re.sub(r"^```(json)?|```$", "", contenido, flags=re.MULTILINE).strip()
        resultado = json.loads(contenido)
        clasificacion = resultado.get("clasificacion", "Revisión")
        if clasificacion not in CLASIFICACIONES_VALIDAS:
            clasificacion = "Revisión"
        return {
            "clasificacion": clasificacion,
            "motivo": resultado.get("motivo", ""),
            "valoracion_criterios_adjudicacion": resultado.get("valoracion_criterios_adjudicacion", ""),
        }
    except Exception as e:
        return {
            "clasificacion": "Revisión",
            "motivo": f"Error al analizar con IA: {e}",
            "valoracion_criterios_adjudicacion": "",
        }


def analizar_solvencia(r, perfil_empresa):
    try:
        texto_pcap = extraer_texto_pdf(r.get("pcap_url"))
    except Exception:
        texto_pcap = ""
    try:
        texto_ppt = extraer_texto_pdf(r.get("ppt_url"))
    except Exception:
        texto_ppt = ""

    resultado = clasificar_solvencia_ia(perfil_empresa, texto_pcap, texto_ppt, r.get("titulo"), r.get("organo"))
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
                r["revision_ia"] = analizar_solvencia(r, perfil_empresa)
                print(f"    -> Revisión IA [{r['folder_id']}]: {r['revision_ia']['clasificacion']}")

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


if __name__ == "__main__":
    main()
