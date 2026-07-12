from flask import Flask, request, render_template, render_template_string
from twilio.twiml.messaging_response import MessagingResponse
import pandas as pd
from datetime import datetime
import os
import psycopg2
from datetime import datetime

# --- COLOCAR ESTO ANTES DE app = Flask(__name__) ---
DATABASE_URL = os.environ.get("DATABASE_URL")

def inicializar_base_de_datos():
    """Crea la tabla de reportes en PostgreSQL si no existe"""
    if not DATABASE_URL:
        print("🔴 No se encontró DATABASE_URL en el entorno.")
        return
    
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reportes (
            id SERIAL PRIMARY KEY,
            fecha_hora TIMESTAMP,
            telefono VARCHAR(50),
            tipo_reporte VARCHAR(50),
            escuela_mesa VARCHAR(100),
            corte_horario VARCHAR(50),
            cantidad_votos VARCHAR(50),
            observaciones TEXT
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("🟢 Tabla de base de datos verificada/creada con éxito.")

# Ejecutamos la inicialización al arrancar la app
inicializar_base_de_datos()

app = Flask(__name__)

# 📊 CONFIGURACIÓN DEL PADRÓN ELECTORAL
PADRON_POR_ESCUELA = {
    "Escuela Nacional": 3500,
    "Colegio San Ignacio": 2800,
    "Escuela Patria": 4200,
    "Escuela Centro": 1500,
    "Colegio Virgen de Loreto": 3100
}
TOTAL_PADRON_GENERAL = sum(PADRON_POR_ESCUELA.values())

# Base de datos temporal en memoria para rastrear el estado de cada fiscal
# Estructura: { 'numero_telefono': { 'estado': 'MENU_PRINCIPAL', 'datos': {} } }
estados_usuarios = {}

# Archivo Excel donde se centralizará todo de forma automática
EXCEL_DB = "registro_votos_realtime.xlsx"

# Crear el archivo Excel con sus columnas si no existe al arrancar el bot
if not os.path.exists(EXCEL_DB):
    df_init = pd.DataFrame(columns=["Fecha/Hora", "Telefono", "Tipo_Reporte", "Escuela_Mesa", "Corte_Horario", "Cantidad_Votos", "Observaciones"])
    df_init.to_excel(EXCEL_DB, index=False)

def guardar_en_excel(telefono, tipo, escuela_mesa="-", corte="-", votos="-", obs="-"):
    """Inserta una nueva fila directamente en la base de datos PostgreSQL de Render"""
    if not DATABASE_URL:
        print("🔴 Error: DATABASE_URL no configurada. No se pudo guardar.")
        return

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        query = """
            INSERT INTO reportes (fecha_hora, telefono, tipo_reporte, escuela_mesa, corte_horario, cantidad_votos, observaciones)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
        """
        
        valores = (
            datetime.now(),
            telefono,
            tipo,
            str(escuela_mesa),
            str(corte),
            str(votos),
            str(obs)
        )
        
        cur.execute(query, valores)
        conn.commit()
        cur.close()
        conn.close()
        print("🟢 Registro guardado correctamente en PostgreSQL.")
        
    except Exception as e:
        print(f"🔴 Error al guardar en PostgreSQL: {e}")
        raise e

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        # Obtener el número de teléfono del fiscal y el mensaje que envió
        telefono = request.values.get("From", "")
        mensaje_recibido = request.values.get("Body", "").strip().lower()
        
        response = MessagingResponse()
        
        # Si el usuario es nuevo o dice 'hola', reiniciamos su estado al menú principal
        if telefono not in estados_usuarios or mensaje_recibido in ["hola", "buen dia", "buenas", "inicio", "reinicio"]:
            estados_usuarios[telefono] = {"estado": "MENU_PRINCIPAL", "datos": {}}
            
            msg = (
                "¡Hola! Bienvenido al Sistema de Monitoreo Electoral 🗳️.\n\n"
                "Por favor, selecciona una opción enviando el número:\n"
                "1️⃣ Votantes hasta el momento\n"
                "2️⃣ Otro motivo (Reportar incidente / Aviso)"
            )
            response.message(msg)
            return str(response)

        # Obtenemos el estado actual de este fiscal específico
        estado_actual = estados_usuarios[telefono]["estado"]

        # --- LÓGICA DE LA MÁQUINA DE ESTADOS ---
        
        if estado_actual == "MENU_PRINCIPAL":
            if mensaje_recibido == "1":
                estados_usuarios[telefono]["estado"] = "SELECCION_HORARIO"
                msg = (
                    "Seleccionaste: *Votantes hasta el momento*.\n\n"
                    "Elige el corte horario enviando el número correspondiente:\n"
                    "1️⃣ 11:00 hs\n"
                    "2️⃣ 15:00 hs\n"
                    "3️⃣ 18:00 hs"
                )
                response.message(msg)
            elif mensaje_recibido == "2":
                estados_usuarios[telefono]["estado"] = "ESPERANDO_INCIDENTE"
                msg = "Por favor, escribe detalladamente el motivo de tu aviso o el incidente que deseas reportar:"
                response.message(msg)
            else:
                response.message("Opción inválida. Por favor, envía *1* o *2*.")

        elif estado_actual == "ESPERANDO_INCIDENTE":
            # Guardamos el incidente reportado en el Excel
            guardar_en_excel(telefono=telefono, tipo="INCIDENTE", obs=request.values.get("Body"))
            # Limpiamos el estado del usuario
            estados_usuarios[telefono] = {"estado": "MENU_PRINCIPAL", "datos": {}}
            response.message("✅ Tu reporte ha sido enviado al centro de cómputos. Muchas gracias por informar. Si necesitas algo más, vuelve a escribir 'Hola'.")

        elif estado_actual == "SELECCION_HORARIO":
            horarios = {"1": "11:00 hs", "2": "15:00 hs", "3": "18:00 hs"}
            if mensaje_recibido in horarios:
                estados_usuarios[telefono]["datos"]["horario"] = horarios[mensaje_recibido]
                estados_usuarios[telefono]["estado"] = "ESPERANDO_MESA"
                response.message("Perfecto. Ahora ingresa tu *Número de Mesa* (ej. 45 o Mesa 45):")
            else:
                response.message("Opción inválida. Elige enviando *1*, *2* o *3*.")

        elif estado_actual == "ESPERANDO_MESA":
            # Guardamos la mesa que escribió el fiscal
            estados_usuarios[telefono]["datos"]["mesa"] = request.values.get("Body").strip()
            estados_usuarios[telefono]["estado"] = "ESPERANDO_VOTOS"
            response.message("¡Entendido! Finalmente, ingresa la *Cantidad Total de Votantes* acumulados hasta este horario (solo números):")

        elif estado_actual == "ESPERANDO_VOTOS":
            if mensaje_recibido.isdigit():
                votos = int(mensaje_recibido)
                datos_fiscal = estados_usuarios[telefono]["datos"]
                
                # Guardamos todo el reporte estructurado en el Excel
                guardar_en_excel(
                    telefono=telefono,
                    tipo="VOTOS_CORTE",
                    escuela_mesa=datos_fiscal["mesa"],
                    corte=datos_fiscal["horario"],
                    votos=votos
                )
                
                # Reiniciamos su estado por si quiere mandar otro reporte más tarde
                estados_usuarios[telefono] = {"estado": "MENU_PRINCIPAL", "datos": {}}
                response.message(f"✅ ¡Datos guardados con éxito!\n\nMesa: {datos_fiscal['mesa']}\nCorte: {datos_fiscal['horario']}\nVotos: {votos}\n\nMuchas gracias por tu reporte. Si deseas realizar otra acción, escribe 'Hola'.")
            else:
                response.message("Por favor, introduce una cantidad válida usando solo números enteros (ej: 142).")

        return str(response)

    except Exception as e:
        # Esto atrapa cualquier error, lo imprime en la consola de Render y te avisa por WhatsApp
        print(f"🔴 ERROR EN WEBHOOK: {e}")
        error_response = MessagingResponse()
        error_response.message(f"Hubo un error interno en el bot: {e}")
        return str(error_response)

@app.route("/", methods=["GET"])
def dashboard():
    reportes = []
    incidencias = []
    
    # Leemos directamente desde la base de datos que usa tu bot
    if os.path.exists(EXCEL_DB):
        try:
            df = pd.read_excel(EXCEL_DB)
            df = df.fillna("")  # Limpiamos los vacíos
            
            # 1. Filtramos las filas que correspondan a reportes numéricos de votos
            df_votos = df[df["Tipo_Reporte"] == "VOTOS_CORTE"]
            # Seleccionamos solo las columnas relevantes para la tabla de votos
            df_votos = df_votos[["Fecha/Hora", "Telefono", "Escuela_Mesa", "Corte_Horario", "Cantidad_Votos"]]
            reportes = df_votos.to_dict(orient="records")
            reportes.reverse()  # El más reciente arriba

            # 2. Filtramos las filas que correspondan a incidentes/avisos (Opción 2)
            df_incidencias = df[df["Tipo_Reporte"] == "INCIDENTE"]
            # Seleccionamos solo las columnas de interés para las alertas
            df_incidencias = df_incidencias[["Fecha/Hora", "Telefono", "Observaciones"]]
            incidencias = df_incidencias.to_dict(orient="records")
            incidencias.reverse()  # El más reciente arriba
            
        except Exception as e:
            print(f"Error al procesar el Excel para el Dashboard: {e}")
            
    return render_template("dashboard.html", reportes=reportes, incidencias=incidencias)

# =========================================================
# VISTA DE PORCENTAJES EN TIEMPO REAL
# =========================================================
@app.route('/estadisticas')
def mostrar_estadisticas():
    # ... Pegá acá adentro TODA la función de estadísticas que te pasé antes ...
    return render_template_string(html_template, ...)


# El cierre clásico de tu archivo queda abajo de todo:
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)

if __name__ == "__main__":
    # Render asigna un puerto automáticamente en la variable de entorno PORT
    puerto = int(os.environ.get("PORT", 8080))
    # Escuchamos en 0.0.0.0 para que Render pueda comunicarse con la app
    app.run(host="0.0.0.0", port=puerto)