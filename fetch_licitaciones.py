import requests
from lxml import etree
import json
import os
from datetime import datetime, timedelta, timezone

NS = {
    'atom': 'http://www.w3.org/2005/Atom',
    'cbc': 'urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2',
    'cac': 'urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2',
    'cac-place-ext': 'urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2',
    'cbc-place-ext': 'urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2',
}

FEED_URL = "https://contrataciondelestado.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3.atom"
CPV_PERMITIDOS = ["71400000", "71410000", "71240000", "71300000"]  # AJUSTAR con tus códigos definitivos
ESTADOS_PERMITIDOS = ["PUB"]  # ampliar si quieres EV/ADJ
VENTANA_HORAS = 24
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

    link_el = entry.find('atom:link', NS)
    link = link_el.get('href') if link_el is not None else None

    nuts_codes = [e.text for e in entry.findall('.//cbc:CountrySubentityCode', NS)]
    es_andalucia = any(c and c.startswith("ES61") for c in nuts_codes)

    cpv_codes = [e.text for e in entry.findall('.//cbc:ItemClassificationCode', NS)]
    cpv_match = any(c in CPV_PERMITIDOS for c in cpv_codes)

    return {
        "folder_id": folder_id,
        "estado": estado_code,
        "titulo": titulo,
        "updated": updated,
        "link": link,
        "es_andalucia": es_andalucia,
        "cpv_match": cpv_match,
        "cpv_codes": cpv_codes,
    }


def fetch_pagina(url):
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    root = etree.fromstring(resp.content)
    return root


def get_next_link(root):
    next_el = root.find('atom:link[@rel="next"]', NS)
    return next_el.get('href') if next_el is not None else None


def main():
    estado = cargar_estado()
    ids_vistos = set(estado.get("ids_vistos", []))

    limite_fecha = datetime.now(timezone.utc) - timedelta(hours=VENTANA_HORAS)
    resultados_filtrados = []
    url_actual = FEED_URL
    pagina = 0

    while url_actual and pagina < MAX_PAGINAS:
        pagina += 1
        root = fetch_pagina(url_actual)
        entries = root.findall('atom:entry', NS)

        if not entries:
            break

        for entry in entries:
            data = parse_entry(entry)
            if not data["updated"]:
                continue
            fecha_entry = datetime.fromisoformat(data["updated"])
            if fecha_entry < limite_fecha:
                # Ya salimos de la ventana temporal deseada
                url_actual = None
                break

            if (data["es_andalucia"]
                and data["cpv_match"]
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
    print(f"Licitaciones nuevas filtradas: {len(resultados_filtrados)}")

    for r in resultados_filtrados:
        print(f"- [{r['folder_id']}] {r['titulo']} | {r['link']}")

    # Salida para consumo posterior (Teams, base de datos, etc.)
    with open("resultado_hoy.json", "w", encoding="utf-8") as f:
        json.dump(resultados_filtrados, f, ensure_ascii=False, indent=2)

    return resultados_filtrados


if __name__ == "__main__":
    main()
