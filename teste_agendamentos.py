import math
import requests
import time
import logging
import os
import json
from datetime import datetime, timedelta, date
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ================================
# 🔐 CONFIGURAÇÕES
# ================================

FEEGOW_API_KEY = os.getenv("FEEGOW_API_KEY")
CALENDAR_ID = os.getenv("CALENDAR_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

BASE_URL = "https://api.feegow.com/v1/api"

PROFISSIONAL_ID = 1
PER_PAGE = 50
TIMEZONE = "America/Sao_Paulo"
INTERVALO_EXECUCAO = 60  # segundos

# Validação básica de segurança
if not FEEGOW_API_KEY:
    raise ValueError("FEEGOW_API_KEY não definida nas variáveis de ambiente")

if not CALENDAR_ID:
    raise ValueError("CALENDAR_ID não definida nas variáveis de ambiente")

if not GOOGLE_CREDENTIALS_JSON:
    raise ValueError("GOOGLE_CREDENTIALS_JSON não definida nas variáveis de ambiente")

HEADERS = {
    "x-access-token": FEEGOW_API_KEY,
    "Accept": "application/json"
}

# ================================
# 🎨 MAPA DE CORES
# ================================

STATUS_COLOR_MAP = {
    3: "10",
    1: "9",
    7: "1",
    2: "5",
    4: "6",
    6: "11",
}

# ================================
# LOGGING
# ================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ================================
# GOOGLE CALENDAR (via JSON em variável)
# ================================

credentials_dict = json.loads(GOOGLE_CREDENTIALS_JSON)

credentials = service_account.Credentials.from_service_account_info(
    credentials_dict,
    scopes=["https://www.googleapis.com/auth/calendar.events"]
)

calendar = build("calendar", "v3", credentials=credentials)

# ================================
# CACHE
# ================================

PACIENTES_CACHE = {}
PROCEDIMENTOS_CACHE = {}

# ================================
# ADD_MONTHS
# ================================

def add_months(data, months):
    month = data.month - 1 + months
    year = data.year + month // 12
    month = month % 12 + 1

    day = min(
        data.day,
        [31,
         29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
         31, 30, 31, 30,
         31, 31, 30, 31, 30, 31][month - 1]
    )

    return date(year, month, day)

# ================================
# BUSCAR STATUS
# ================================

def buscar_mapa_status():
    resp = requests.get(f"{BASE_URL}/appoints/status", headers=HEADERS)
    status_map = {}

    if resp.status_code == 200:
        data = resp.json()
        for item in data.get("content", []):
            status_map[item["id"]] = item["status"]

    return status_map

# ================================
# BUSCAR AGENDAMENTOS
# ================================

def buscar_agendamentos(data_start, data_end):
    agendamentos_por_id = {}
    page = 1
    total_pages = None

    while True:
        params = {
            "profissional_id": PROFISSIONAL_ID,
            "data_start": data_start.strftime("%Y-%m-%d"),
            "data_end": data_end.strftime("%Y-%m-%d"),
            "page": page,
            "per_page": PER_PAGE
        }

        resp = requests.get(
            f"{BASE_URL}/appoints/search",
            headers=HEADERS,
            params=params
        )

        if resp.status_code != 200:
            logging.error(f"Erro Feegow: {resp.text}")
            break

        data = resp.json()
        content = data.get("content", [])
        total = data.get("total", 0)

        if total_pages is None:
            total_pages = max(1, math.ceil(total / PER_PAGE))

        for ag in content:
            ag_id = ag.get("agendamento_id")
            if ag_id:
                agendamentos_por_id[str(ag_id)] = ag

        if page >= total_pages:
            break

        page += 1

    return list(agendamentos_por_id.values())

# ================================
# BUSCAR EVENTO GOOGLE
# ================================

def buscar_evento_por_feegow_id(feegow_id):
    eventos = calendar.events().list(
        calendarId=CALENDAR_ID,
        privateExtendedProperty=f"feegow_id={feegow_id}",
        maxResults=1
    ).execute()

    items = eventos.get("items", [])
    return items[0] if items else None

# ================================
# VERIFICAR ATUALIZAÇÃO
# ================================

def evento_precisa_atualizar(evento_google, novo_evento):

    if not evento_google:
        return True

    campos = ["summary", "description", "colorId"]

    for campo in campos:
        if evento_google.get(campo) != novo_evento.get(campo):
            return True

    if evento_google["start"].get("dateTime") != novo_evento["start"].get("dateTime"):
        return True

    if evento_google["end"].get("dateTime") != novo_evento["end"].get("dateTime"):
        return True

    return False

# ================================
# SINCRONIZAÇÃO
# ================================

def migrar_agenda():

    hoje = date.today()
    data_start = add_months(hoje, -1)
    data_end = add_months(hoje, 4)

    status_map = buscar_mapa_status()
    agendamentos = buscar_agendamentos(data_start, data_end)

    criados = 0
    atualizados = 0

    for ag in agendamentos:
        try:
            feegow_id = ag.get("agendamento_id")
            status_id = ag.get("status_id")
            status_nome = status_map.get(status_id, "")

            evento_existente = buscar_evento_por_feegow_id(str(feegow_id))

            data = ag.get("data")
            horario = ag.get("horario")
            if not data or not horario:
                continue

            inicio_dt = datetime.strptime(
                f"{data} {horario}",
                "%d-%m-%Y %H:%M:%S"
            )
            fim_dt = inicio_dt + timedelta(minutes=30)

            titulo = f"{feegow_id} - {status_nome}"

            evento = {
                "summary": titulo,
                "description": f"Status: {status_nome}",
                "start": {
                    "dateTime": inicio_dt.isoformat(),
                    "timeZone": TIMEZONE
                },
                "end": {
                    "dateTime": fim_dt.isoformat(),
                    "timeZone": TIMEZONE
                },
                "extendedProperties": {
                    "private": {
                        "feegow_id": str(feegow_id)
                    }
                }
            }

            if status_id in STATUS_COLOR_MAP:
                evento["colorId"] = STATUS_COLOR_MAP[status_id]

            if evento_existente:
                if evento_precisa_atualizar(evento_existente, evento):
                    calendar.events().update(
                        calendarId=CALENDAR_ID,
                        eventId=evento_existente["id"],
                        body=evento
                    ).execute()
                    atualizados += 1
            else:
                calendar.events().insert(
                    calendarId=CALENDAR_ID,
                    body=evento
                ).execute()
                criados += 1

        except Exception as e:
            logging.error(f"Erro ao processar agendamento: {e}")

    logging.info(f"Criados: {criados} | Atualizados: {atualizados}")

# ================================
# LOOP CONTÍNUO
# ================================

def main():
    logging.info("🚀 Worker Feegow → Google iniciado")

    while True:
        inicio = time.time()

        try:
            migrar_agenda()
        except Exception as e:
            logging.error(f"Erro geral: {e}")

        tempo_execucao = time.time() - inicio
        espera = max(0, INTERVALO_EXECUCAO - tempo_execucao)

        logging.info(f"Aguardando {espera:.0f}s para próxima execução")
        time.sleep(espera)

if __name__ == "__main__":
    main()
