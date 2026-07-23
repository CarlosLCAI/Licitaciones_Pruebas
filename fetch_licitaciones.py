import requests
from lxml import etree
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

# Revisión de solvencia por IA (opcional): se activa solo si las variables de entorno
# SOLVENCIA_EMPRESA y ANTHROPIC_API_KEY están configuradas (secrets de GitHub Actions).
# Usa la API directa de Anthropic (Claude), mandando los PDF completos como documentos
# (no texto extraído) para que Claude pueda leer visualmente páginas escaneadas sin capa
# de texto — pypdf se quedaría en blanco con esos documentos.
ANTHROPIC_API_ENDPOINT = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
ANTHROPIC_VERSION = "2023-06-01"

PROMPT_SISTEMA_INFORME = (
    "Eres un asistente que analiza el PCAP de una licitación pública española de redacción de "
    "instrumento urbanístico, para el equipo de CAI Consultores (empresa de "
    "arquitectura/ingeniería). Si el documento no tiene capa de texto (páginas escaneadas o "
    "rasterizadas), léelo igualmente mediante reconocimiento visual completo de la página — no "
    "omitas contenido por esa causa.\n\n"
    "Estructura tu respuesta en estas 4 secciones, exactamente en este orden, y omite cualquier "
    "apartado de datos generales del contrato (objeto, presupuesto, plazos administrativos):\n\n"
    "1. Solvencia económico-financiera\n"
    "Medios exigidos y forma de acreditación.\n\n"
    "2. Solvencia técnica / Equipo mínimo\n"
    "Lista cada perfil exigido con su titulación y su experiencia mínima. Si el PCAP ofrece "
    "varias alternativas de experiencia para un mismo perfil, preséntalas como una lista de "
    "opciones unidas por \"o\" (no como texto corrido). Usa este formato exacto:\n"
    "* Perfil (nº personas): titulación\n"
    "   * Experiencia opción A, o\n"
    "   * Experiencia opción B\n\n"
    "3. Criterios de adjudicación\n"
    "Desglosa todos los criterios automáticos y de juicio de valor con su puntuación. Si existe "
    "un criterio o subapartado de \"mejora de la experiencia del equipo\" / \"experiencia adicional "
    "del equipo\" distinto del mínimo de solvencia, márcalo explícitamente al final de ese bloque "
    "con \"⚠ REVISAR — criterio de mejora de equipo, no confundir con el mínimo de solvencia\", sin "
    "desarrollarlo salvo que se pida expresamente.\n\n"
    "4. Valoración para CAI Consultores\n"
    "Puntos a favor y puntos a verificar internamente, en relación con el perfil de la empresa que "
    "se te proporciona (equipo, experiencia LISTA/LOUA, capacidad de subcontratar perfiles no "
    "cubiertos).\n\n"
    "Responde en español, en Markdown legible, usando los encabezados y viñetas indicados. No "
    "incluyas ningún comentario fuera de estas 4 secciones."
)


def descargar_pdf_base64(url, max_bytes=32 * 1024 * 1024):
    """Descarga el PDF y lo devuelve codificado en base64, para mandarlo entero a Claude
    como documento (no como texto extraído) — así Claude puede leer visualmente páginas
    escaneadas sin capa de texto. max_bytes es el límite de tamaño de documento de la API."""
    if not url:
        return None
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    contenido = resp.content
    if len(contenido) > max_bytes:
        print(f"    Aviso: PDF de {len(contenido)} bytes supera el máximo de {max_bytes}; se omite.")
        return None
    return base64.b64encode(contenido).decode("ascii")


def generar_informe_licitacion_ia(perfil_empresa, pcap_b64, titulo, organo):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"informe": "No hay ANTHROPIC_API_KEY configurada para llamar a Claude."}
    if not pcap_b64:
        return {"informe": "No se pudo descargar el PCAP."}

    fecha_hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    texto_intro = (
        f"FECHA DE HOY: {fecha_hoy}\n"
        f"LICITACIÓN: {titulo}\n"
        f"ÓRGANO: {organo}\n\n"
        f"PERFIL DE LA EMPRESA (CAI Consultores):\n{perfil_empresa}\n\n"
        f"A continuación se adjunta el PCAP de esta licitación como documento PDF. "
        f"Genera el informe según las instrucciones del sistema."
    )

    contenido_mensaje = [
        {"type": "text", "text": texto_intro},
        {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": pcap_b64},
        },
    ]

    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 4096,
        "system": PROMPT_SISTEMA_INFORME,
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
        f"PCAP: {len(pcap_b64)} car. base64"
    )

    try:
        resp = requests.post(ANTHROPIC_API_ENDPOINT, headers=headers, json=payload, timeout=180)
        if not resp.ok:
            print(f"    Respuesta de error de Anthropic (HTTP {resp.status_code}): {resp.text[:2000]}")
        resp.raise_for_status()
        data = resp.json()
        bloque_texto = next(b["text"] for b in data["content"] if b.get("type") == "text")
        return {"informe": bloque_texto.strip()}
    except Exception as e:
        return {"informe": f"Error al generar el informe con IA: {e}"}


def analizar_licitacion_ia(r, perfil_empresa):
    try:
        pcap_b64 = descargar_pdf_base64(r.get("pcap_url"))
    except Exception:
        pcap_b64 = None

    resultado = generar_informe_licitacion_ia(perfil_empresa, pcap_b64, r.get("titulo"), r.get("organo"))
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


if __name__ == "__main__":
    main()
