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

PROFISSIONAL_ID = int(os.getenv("PROFISSIONAL_ID"))
PER_PAGE = 50
TIMEZONE = "America/Sao_Paulo"
INTERVALO_EXECUCAO = 15 * 60  # 15 minutos

if not FEEGOW_API_KEY:
    raise ValueError("FEEGOW_API_KEY não definida")

if not CALENDAR_ID:
    raise ValueError("CALENDAR_ID não definida")

if not GOOGLE_CREDENTIALS_JSON:
    raise ValueError("GOOGLE_CREDENTIALS_JSON não definida")

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
# 📆 ADD_MONTHS
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

hoje = date.today()
data_start = add_months(hoje, -1)
data_end = add_months(hoje, 4)

# ================================
# GOOGLE CALENDAR
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
# MAPA STATUS
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

def buscar_agendamentos():
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
# BUSCAR TODOS EVENTOS GOOGLE
# ================================

def buscar_todos_eventos_feegow():
    eventos_feegow = []
    page_token = None

    while True:
        eventos = calendar.events().list(
            calendarId=CALENDAR_ID,
            privateExtendedProperty="feegow_id",
            pageToken=page_token
        ).execute()

        for item in eventos.get("items", []):
            feegow_id = item.get("extendedProperties", {}).get("private", {}).get("feegow_id")
            if feegow_id:
                eventos_feegow.append((feegow_id, item["id"]))

        page_token = eventos.get("nextPageToken")
        if not page_token:
            break

    return eventos_feegow

# ================================
# BUSCAR EVENTO POR ID
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
# VERIFICAR ALTERAÇÃO
# ================================

def evento_precisa_atualizar(evento_google, novo_evento):

    if not evento_google:
        return True

    campos = ["summary", "description", "colorId", "transparency"]

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
    logging.info("🔄 Iniciando sincronização...")

    status_map = buscar_mapa_status()
    agendamentos = buscar_agendamentos()
    ids_feegow_atuais = set(
        str(ag["agendamento_id"])
        for ag in agendamentos
        if ag.get("agendamento_id")
    )

    criados = 0
    atualizados = 0
    removidos = 0

    # ====================
    # CRIAR / ATUALIZAR
    # ====================

    for ag in agendamentos:
        try:
            feegow_id = str(ag.get("agendamento_id"))
            status_id = ag.get("status_id")
            status_nome = status_map.get(status_id, "")

            evento_existente = buscar_evento_por_feegow_id(feegow_id)

            data = ag.get("data")
            horario = ag.get("horario")
            if not data or not horario:
                continue

            inicio_dt = datetime.strptime(
                f"{data} {horario}",
                "%d-%m-%Y %H:%M:%S"
            )
            fim_dt = inicio_dt + timedelta(minutes=30)

            paciente_nome = buscar_nome_paciente(ag.get("paciente_id"))
            procedimento_nome = buscar_nome_procedimento(ag.get("procedimento_id"))
            motivo = procedimento_nome if procedimento_nome else "Consulta"
            assistente = ag.get("agendado_por", "")

            titulo = f"{paciente_nome} - {motivo}"
            colorId = STATUS_COLOR_MAP.get(status_id)
            transparency = "opaque"

            if status_id == 3:
                titulo = f"[ATENDIDO] {paciente_nome} - {motivo}"
                transparency = "transparent"

            evento = {
                "summary": titulo,
                "description": (
                    f"Paciente: {paciente_nome}\n"
                    f"Motivo: {motivo}\n"
                    f"Status: {status_nome}\n"
                    f"Concierge: {assistente}"
                ),
                "start": {
                    "dateTime": inicio_dt.isoformat(),
                    "timeZone": TIMEZONE
                },
                "end": {
                    "dateTime": fim_dt.isoformat(),
                    "timeZone": TIMEZONE
                },
                "transparency": transparency,
                "extendedProperties": {
                    "private": {
                        "feegow_id": feegow_id
                    }
                }
            }

            if colorId:
                evento["colorId"] = colorId

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
            logging.error(f"Erro ao processar {feegow_id}: {e}")

    # ====================
    # REMOVER EXCLUÍDOS
    # ====================

    logging.info("🔍 Verificando exclusões...")

    eventos_google = buscar_todos_eventos_feegow()

    for feegow_id, google_event_id in eventos_google:
        if feegow_id not in ids_feegow_atuais:
            try:
                calendar.events().delete(
                    calendarId=CALENDAR_ID,
                    eventId=google_event_id
                ).execute()
                removidos += 1
                logging.info(f"🗑️ Removido: {feegow_id}")
            except Exception as e:
                logging.error(f"Erro ao remover {feegow_id}: {e}")

    logging.info(f"✅ Criados: {criados}")
    logging.info(f"🔁 Atualizados: {atualizados}")
    logging.info(f"🗑️ Removidos: {removidos}")
    logging.info("🎉 Sincronização finalizada")

# ================================
# LOOP
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

        logging.info(f"Aguardando {espera:.0f}s")
        time.sleep(espera)

if __name__ == "__main__":
    main()
