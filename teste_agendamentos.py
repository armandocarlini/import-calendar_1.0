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
INTERVALO_EXECUCAO = 15 * 60  # 15 minutos em segundos

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
# 📅 FUNÇÃO DE DATA (SEM DEPENDÊNCIA)
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
# 📆 INTERVALO AUTOMÁTICO (FEGOW)
# ================================
data_start = date.today()

# Feegow exige intervalo MENOR que 6 meses
data_end = add_months(data_start, 6) - timedelta(days=1)

print("data-start:", data_start.strftime("%Y-%m-%d"))
print("data-end:", data_end.strftime("%Y-%m-%d"))

# ================================
# 🔑 GOOGLE CALENDAR
# ================================
credentials = service_account.Credentials.from_service_account_file(
    GOOGLE_CREDENTIALS_JSON,
    scopes=["https://www.googleapis.com/auth/calendar.events"]
)

calendar = build("calendar", "v3", credentials=credentials)

# ================================
# 🧠 CACHE
# ================================
PACIENTES_CACHE = {}
PROCEDIMENTOS_CACHE = {}

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
            print("❌ Erro Feegow:", resp.text)
            break

        data = resp.json()
        content = data.get("content", [])
        total = data.get("total", 0)

        if total_pages is None:
            total_pages = max(1, math.ceil(total / PER_PAGE))
            print(f"📊 Total registros: {total}")
            print(f"📄 Total páginas: {total_pages}")

        print(f"📄 Página {page} → {len(content)} registros")

        for ag in content:
            ag_id = ag.get("agendamento_id")
            if ag_id:
                agendamentos_por_id[str(ag_id)] = ag

        if page >= total_pages:
            break

        page += 1

    return list(agendamentos_por_id.values())

# ================================
# 👤 BUSCAR NOME DO PACIENTE
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
# 🧾 BUSCAR NOME DO PROCEDIMENTO
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
# 📅 SINCRONIZAR AGENDA
# ================================
def migrar_agenda():
    agendamentos = buscar_agendamentos()
    print(f"\n🔎 Total único após deduplicação: {len(agendamentos)}\n")

    criados = 0
    atualizados = 0

    for ag in agendamentos:
        try:
            feegow_id = ag.get("agendamento_id")
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
            assistente_agendamento = ag.get("agendado_por", "")


            evento = {
                "summary": f"{paciente_nome} - {motivo}",
                "description": (
                    f"Paciente: {paciente_nome}\n"
                    f"Motivo: {motivo}\n"
                    f"Concierge: {assistente_agendamento}\n"

                ),
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

            if evento_existente:
                calendar.events().update(
                    calendarId=CALENDAR_ID,
                    eventId=evento_existente["id"],
                    body=evento
                ).execute()
                atualizados += 1
                print(f"🔁 Atualizado: {paciente_nome} - {motivo}")
            else:
                calendar.events().insert(
                    calendarId=CALENDAR_ID,
                    body=evento
                ).execute()
                criados += 1
                print(f"✅ Criado: {paciente_nome} - {motivo}")

        except Exception as e:
            print("⚠️ Erro:", e)

    print("\n🎉 Sincronização finalizada")
    print(f"✅ Criados: {criados}")
    print(f"🔁 Atualizados: {atualizados}")


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
