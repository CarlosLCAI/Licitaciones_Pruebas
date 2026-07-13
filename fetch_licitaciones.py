import requests
from lxml import etree
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
CPV_PERMITIDOS = ["71400000", "71222000", "71240000", "71510000", "90712100"]  # AJUSTAR con tus códigos definitivos
ESTADOS_PERMITIDOS = ["PUB"]  # ampliar si quieres EV/ADJ
VENTANA_HORAS = 30
MAX_PAGINAS = 30
ESTADO_FILE = "estado.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (CAI-Consultores-Monitor/1.0)"}


def cargar_estado():
    if os.path.exists(ESTADO_FILE):
        with open(ESTADO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"ids_vistos": []}


def guardar_estado(estado):
    with open(ESTADO_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


def parse_entry(entry):
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

    nuts_codes = [e.text for e in entry.findall('.//cbc:CountrySubentityCode', NS)]
    es_andalucia = any(c and c.startswith("ES61") for c in nuts_codes)

    cpv_codes = [e.text for e in entry.findall('.//cbc:ItemClassificationCode', NS)]
    cpv_match = any(c in CPV_PERMITIDOS for c in cpv_codes)

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
        "fecha_limite": fecha_limite,
    }


import time

def fetch_pagina(url, intentos=3):
    for intento in range(1, intentos + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return etree.fromstring(resp.content)
        except requests.exceptions.RequestException as e:
            print(f"Intento {intento}/{intentos} fallido para {url}: {e}")
            if intento == intentos:
                raise
            time.sleep(5 * intento)  # espera creciente: 5s, 10s, 15s...


def get_next_link(root):
    next_el = root.find('atom:link[@rel="next"]', NS)
    return next_el.get('href') if next_el is not None else None
    
def notificar_teams(resultados, paginas, total_entries):
    webhook_url = os.environ.get("TEAMS_WEBHOOK_URL")
    if not webhook_url:
        print("Aviso: no se ha configurado TEAMS_WEBHOOK_URL, se omite notificación.")
        return

    ahora = datetime.now(timezone.utc).strftime("%H:%M UTC")

    texto_resumen = (
        f"**Lectura PLASP Andalucía** ({ahora})\n\n"
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
    estado = cargar_estado()
    ids_vistos = set(estado.get("ids_vistos", []))

    limite_fecha = datetime.now(timezone.utc) - timedelta(hours=VENTANA_HORAS)
    resultados_filtrados = []
    url_actual = FEED_URL
    pagina = 0
    total_entries_leidas = 0

    while url_actual and pagina < MAX_PAGINAS:
        pagina += 1
        root = fetch_pagina(url_actual)
        entries = root.findall('atom:entry', NS)

        if not entries:
            break

        for entry in entries:
            total_entries_leidas += 1
            data = parse_entry(entry)
            if not data["updated"]:
                continue
            fecha_entry = datetime.fromisoformat(data["updated"])
            if fecha_entry < limite_fecha:
                url_actual = None
                break

            if (data["es_andalucia"]
                and (not CPV_PERMITIDOS or data["cpv_match"])
                and data["estado"] in ESTADOS_PERMITIDOS
                and data["folder_id"] not in ids_vistos):
                resultados_filtrados.append(data)
                ids_vistos.add(data["folder_id"])

        if url_actual is None:
            break

        url_actual = get_next_link(root)

    estado["ids_vistos"] = list(ids_vistos)
    guardar_estado(estado)

    print(f"Páginas leídas: {pagina}")
    print(f"Licitaciones leídas (total entries): {total_entries_leidas}")
    print(f"Licitaciones nuevas filtradas: {len(resultados_filtrados)}")
    metadata_lectura = {
        "fecha_hora": datetime.now(timezone.utc).isoformat(),
        "paginas": pagina,
        "total_entries_leidas": total_entries_leidas,
        "nuevas_filtradas": len(resultados_filtrados),
    }
    with open("ultima_lectura.json", "w", encoding="utf-8") as f:
        json.dump(metadata_lectura, f, ensure_ascii=False, indent=2)

    for r in resultados_filtrados:
        print(f"- [{r['folder_id']}] {r['titulo']} | {r['link']}")

    with open("resultado_hoy.json", "w", encoding="utf-8") as f:
        json.dump(resultados_filtrados, f, ensure_ascii=False, indent=2)

    fecha_captura_hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for r in resultados_filtrados:
        r["fecha_captura"] = fecha_captura_hoy

    HISTORICO_FILE = "historico.json"
    if os.path.exists(HISTORICO_FILE):
        with open(HISTORICO_FILE, "r", encoding="utf-8") as f:
            historico = json.load(f)
    else:
        historico = []

    historico.extend(resultados_filtrados)

    with open(HISTORICO_FILE, "w", encoding="utf-8") as f:
        json.dump(historico, f, ensure_ascii=False, indent=2)

    notificar_teams(resultados_filtrados, pagina, total_entries_leidas)

    return resultados_filtrados


if __name__ == "__main__":
    main()
