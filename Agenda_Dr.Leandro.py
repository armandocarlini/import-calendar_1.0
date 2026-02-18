import math
import requests
import logging
import os
import json
import threading
from datetime import datetime, timedelta, date
from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

# ================================
# 🚀 FLASK APP
# ================================

app = Flask(__name__)

# 🔒 LOCK PARA EVITAR EXECUÇÕES SIMULTÂNEAS
sync_lock = threading.Lock()

# ================================
# 🔐 CONFIGURAÇÕES
# ================================

FEEGOW_API_KEY = os.getenv("FEEGOW_API_KEY")
CALENDAR_ID = os.getenv("CALENDAR_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

PROFISSIONAL_ID = int(os.getenv("PROFISSIONAL_ID"))

BASE_URL = "https://api.feegow.com/v1/api"
PER_PAGE = 50
TIMEZONE = "America/Sao_Paulo"

# ================================
# 🔎 VALIDAÇÕES
# ================================

if not FEEGOW_API_KEY:
    raise ValueError("FEEGOW_API_KEY não definida")

if not CALENDAR_ID:
    raise ValueError("CALENDAR_ID não definida")

if not PROFISSIONAL_ID:
    raise ValueError("PROFISSIONAL_ID não definido")

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
# 📆 FUNÇÃO ADD_MONTHS
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
# 📆 INTERVALO INTELIGENTE
# ================================

hoje = date.today()
data_start = add_months(hoje, -1)
data_end = add_months(hoje, 4)

# ================================
# 🔐 GOOGLE CALENDAR
# ================================

if os.getenv("GOOGLE_CREDENTIALS_JSON"):
    credentials_dict = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
    credentials_dict["private_key"] = credentials_dict["private_key"].replace("\\n", "\n")

    credentials = service_account.Credentials.from_service_account_info(
        credentials_dict,
        scopes=["https://www.googleapis.com/auth/calendar.events"]
    )
else:
    credentials = service_account.Credentials.from_service_account_file(
        "credentials.json",
        scopes=["https://www.googleapis.com/auth/calendar.events"]
    )

calendar = build("calendar", "v3", credentials=credentials)

# ================================
# 🧠 CACHE
# ================================

PACIENTES_CACHE = {}
PROCEDIMENTOS_CACHE = {}

# ================================
# 📌 MAPA STATUS
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
# 📡 BUSCAR AGENDAMENTOS
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
            logging.info(f"📊 Total registros: {total}")
            logging.info(f"📄 Total páginas: {total_pages}")

        for ag in content:
            ag_id = ag.get("agendamento_id")
            if ag_id:
                agendamentos_por_id[str(ag_id)] = ag

        if page >= total_pages:
            break

        page += 1

    return list(agendamentos_por_id.values())

# ================================
# 👤 BUSCAR PACIENTE
# ================================

def buscar_nome_paciente(paciente_id):
    if not paciente_id:
        return "Paciente"

    if paciente_id in PACIENTES_CACHE:
        return PACIENTES_CACHE[paciente_id]

    resp = requests.get(
        f"{BASE_URL}/patient/search",
        headers=HEADERS,
        params={
            "paciente_id": paciente_id,
            "programa_saude": 1,
            "photo": 0
        }
    )

    nome = "Paciente"

    if resp.status_code == 200:
        data = resp.json()
        if data.get("success") and data.get("content"):
            nome = data["content"].get("nome", nome)

    PACIENTES_CACHE[paciente_id] = nome
    return nome

# ================================
# 🧾 BUSCAR PROCEDIMENTO
# ================================

def buscar_nome_procedimento(procedimento_id):
    if not procedimento_id:
        return None

    if procedimento_id in PROCEDIMENTOS_CACHE:
        return PROCEDIMENTOS_CACHE[procedimento_id]

    resp = requests.get(
        f"{BASE_URL}/procedures/list",
        headers=HEADERS,
        json={"procedimento_id": procedimento_id}
    )

    nome = None

    if resp.status_code == 200:
        data = resp.json()
        if data.get("success") and data.get("content"):
            nome = data["content"][0].get("nome")

    PROCEDIMENTOS_CACHE[procedimento_id] = nome
    return nome

# ================================
# 🔍 BUSCAR EVENTO GOOGLE
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
# 📅 SINCRONIZAÇÃO
# ================================

def migrar_agenda():
    logging.info("🔄 Iniciando sincronização via webhook")

    status_map = buscar_mapa_status()
    agendamentos = buscar_agendamentos()

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
                        "feegow_id": str(feegow_id)
                    }
                }
            }

            if colorId:
                evento["colorId"] = colorId

            if evento_existente:
                calendar.events().update(
                    calendarId=CALENDAR_ID,
                    eventId=evento_existente["id"],
                    body=evento
                ).execute()
                atualizados += 1
                logging.info(f"🔁 Atualizado: {titulo}")
            else:
                calendar.events().insert(
                    calendarId=CALENDAR_ID,
                    body=evento
                ).execute()
                criados += 1
                logging.info(f"✅ Criado: {titulo}")

        except Exception as e:
            logging.error(f"Erro ao sincronizar: {e}")

    logging.info("🎉 Sincronização finalizada")
    logging.info(f"✅ Criados: {criados}")
    logging.info(f"🔁 Atualizados: {atualizados}")

# ================================
# 🌐 WEBHOOK
# ================================

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        logging.info("📩 Webhook recebido")

        if sync_lock.locked():
            logging.info("⚠️ Sincronização já em andamento. Webhook ignorado.")
            return "Já sincronizando", 200

        with sync_lock:
            migrar_agenda()

        return "OK", 200

    except Exception as e:
        logging.error(f"Erro no webhook: {e}")
        return "Erro interno", 500

# ================================
# 🚀 START SERVER
# ================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
