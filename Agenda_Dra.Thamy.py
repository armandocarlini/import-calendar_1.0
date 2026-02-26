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
# 📆 FUNÇÃO ADD_MONTHS SEGURA
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
data_end   = add_months(hoje, 4)

print("data-start:", data_start.strftime("%Y-%m-%d"))
print("data-end:", data_end.strftime("%Y-%m-%d"))


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
# 🧠 CACHE
# ================================
PACIENTES_CACHE = {}
PROCEDIMENTOS_CACHE = {}

# ================================
# 📌 MAPA DE STATUS
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
            print("❌ Erro Feegow:", resp.text)
            break

        data = resp.json()
        content = data.get("content", [])
        total = data.get("total", 0)

        if total_pages is None:
            total_pages = max(1, math.ceil(total / PER_PAGE))
            print(f"📊 Total registros: {total}")
            print(f"📄 Total páginas: {total_pages}")

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
# BUSCAR TODOS EVENTOS GOOGLE
# ================================

def buscar_todos_eventos_feegow():
    eventos_feegow = []
    page_token = None

    while True:
        eventos = calendar.events().list(
            calendarId=CALENDAR_ID,
            pageToken=page_token
        ).execute()

        for item in eventos.get("items", []):
            feegow_id = (
                item.get("extendedProperties", {})
                    .get("private", {})
                    .get("feegow_id")
            )

            if feegow_id:
                eventos_feegow.append((feegow_id, item["id"]))

        page_token = eventos.get("nextPageToken")
        if not page_token:
            break

    return eventos_feegow

# ================================
# 🔍 BUSCAR EVENTO GOOGLE POR ID
# ================================
def buscar_eventos_por_feegow_id(feegow_id):
    eventos = calendar.events().list(
        calendarId=CALENDAR_ID,
        privateExtendedProperty=f"feegow_id={feegow_id}"
    ).execute()

    return eventos.get("items", [])

# ================================
# 🧠 VERIFICAR SE PRECISA ATUALIZAR
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
# 📅 SINCRONIZAR
# ================================
def limpar_duplicados_paciente_dia():
    logging.info("🧹 Verificando duplicados por paciente_id + dia")

    eventos = calendar.events().list(
        calendarId=CALENDAR_ID,
        maxResults=2500
    ).execute().get("items", [])

    mapa = {}

    for evento in eventos:
        private = evento.get("extendedProperties", {}).get("private", {})
        paciente_id = private.get("paciente_id")

        if not paciente_id:
            continue

        start = evento.get("start", {}).get("dateTime")
        if not start:
            continue

        dia = start.split("T")[0]

        chave = f"{paciente_id}_{dia}"

        if chave not in mapa:
            mapa[chave] = [evento]
        else:
            mapa[chave].append(evento)

    removidos = 0

    for chave, lista_eventos in mapa.items():
        if len(lista_eventos) > 1:
            # Mantém apenas o primeiro
            lista_eventos.sort(key=lambda x: x["created"])
            manter = lista_eventos[0]

            for evento in lista_eventos[1:]:
                logging.warning(
                    f"🗑️ Removendo duplicado paciente_id={evento['extendedProperties']['private'].get('paciente_id')} "
                    f"dia={evento['start']['dateTime']}"
                )

                calendar.events().delete(
                    calendarId=CALENDAR_ID,
                    eventId=evento["id"]
                ).execute()

                removidos += 1

    logging.info(f"🧹 Total duplicados removidos: {removidos}")

def migrar_agenda():
    
    limpar_duplicados_paciente_dia() 

    status_map = buscar_mapa_status()
    agendamentos = buscar_agendamentos()

    criados = 0
    atualizados = 0
    removidos = 0

    for ag in agendamentos:
        try:
            feegow_id = ag.get("agendamento_id")
            status_id = ag.get("status_id")
            status_nome = status_map.get(status_id, "")

            eventos_existentes = buscar_eventos_por_feegow_id(str(feegow_id))

            evento_existente = None

            if len(eventos_existentes) > 1:
                # mantém apenas o mais antigo
                eventos_existentes.sort(key=lambda x: x["created"])
                evento_existente = eventos_existentes[0]

                for evento in eventos_existentes[1:]:
                    logging.warning(f"🗑️ Removendo duplicado feegow_id={feegow_id}")
                    calendar.events().delete(
                        calendarId=CALENDAR_ID,
                        eventId=evento["id"]
                    ).execute()

            elif len(eventos_existentes) == 1:
                evento_existente = eventos_existentes[0]

            # ============================
            # 🔴 REMOVER SE DESMARCADO (11 ou 16)
            # ============================
            STATUS_CANCELADOS = {11, 16}

            # Garantir que status_id seja inteiro
            try:
                status_id = int(status_id) if status_id is not None else None
            except:
                status_id = None

            if status_id in STATUS_CANCELADOS:
                if evento_existente:
                    logging.warning(
                        f"🗑️ EXCLUINDO → feegow_id={feegow_id} | "
                        f"status_id={status_id} | status_nome={status_nome}"
                    )

                    calendar.events().delete(
                        calendarId=CALENDAR_ID,
                        eventId=evento_existente["id"]
                    ).execute()

                    removidos += 1
                    logging.info(f"🗑️ Removido (Cancelado no Feegow): {feegow_id}")

                continue

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
                        "feegow_id": str(feegow_id),
                        "paciente_id": str(ag.get("paciente_id"))
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
                    print(f"🔁 Atualizado: {titulo}")
                else:
                    print(f"⏭️ Sem alteração: {titulo}")
            else:
                calendar.events().insert(
                    calendarId=CALENDAR_ID,
                    body=evento
                ).execute()
                criados += 1
                print(f"✅ Criado: {titulo}")

        except Exception as e:
            print("⚠️ Erro:", e)
            logging.error(f"Erro ao processar agendamento {feegow_id}: {e}")

    logging.info(f"✅ Criados: {criados}")
    logging.info(f"🔁 Atualizados: {atualizados}")
    logging.info(f"🗑️ Removidos: {removidos}")
    logging.info("🎉 Sincronização finalizada")


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
