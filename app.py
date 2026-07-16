from flask import Flask, request, render_template, render_template_string, jsonify, Response
from twilio.twiml.messaging_response import MessagingResponse
import os
import psycopg2
from datetime import datetime
import re
from functools import wraps

# --- CONFIGURACIÓN DE BASE DE DATOS ---
DATABASE_URL = os.environ.get("DATABASE_URL")

def migrar_base_de_datos_existente():
    """Agrega las columnas faltantes a la tabla 'reportes' si ya existía de antes"""
    if not DATABASE_URL:
        return
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        # Agregamos las columnas una por una si no existen
        cur.execute("""
            ALTER TABLE reportes ADD COLUMN IF NOT EXISTS escuela VARCHAR(255);
            ALTER TABLE reportes ADD COLUMN IF NOT EXISTS mesa VARCHAR(50);
            ALTER TABLE reportes ADD COLUMN IF NOT EXISTS votos INTEGER;
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("🟢 Migración completada: Columnas 'escuela', 'mesa' y 'votos' verificadas/agregadas.")
    except Exception as e:
        print(f"🔴 Error durante la migración de columnas: {e}")

# Ejecutamos primero la migración y luego la inicialización común
migrar_base_de_datos_existente()
inicializar_base_de_datos()

def inicializar_base_de_datos():
    """Crea la tabla de reportes en PostgreSQL si no existe con todas las columnas correctas"""
    if not DATABASE_URL:
        print("🔴 No se encontró DATABASE_URL en el entorno.")
        return
    
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    # Creamos la tabla asegurándonos de que 'escuela', 'mesa' y 'votos' existan
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reportes (
            id SERIAL PRIMARY KEY,
            fecha_hora TIMESTAMP,
            telefono VARCHAR(50),
            tipo_reporte VARCHAR(50),
            escuela_mesa VARCHAR(100),
            corte_horario VARCHAR(50),
            cantidad_votos VARCHAR(50),
            observaciones TEXT,
            escuela VARCHAR(255),
            mesa VARCHAR(50),
            votos INTEGER
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("🟢 Tabla de base de datos verificada/creada con éxito en PostgreSQL.")

# Ejecutamos la inicialización al arrancar la app
inicializar_base_de_datos()

app = Flask(__name__)

# --- CONTROL DE ACCESO (PASSWORD) ---
def check_auth(username, password):
    # Credenciales de acceso
    return username == 'admin' and password == 'ELEC26'

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return Response(
                'No autorizado. Necesitás credenciales para acceder.', 401,
                {'WWW-Authenticate': 'Basic realm="Login Required"'}
            )
        return f(*args, **kwargs)
    return decorated

# 📊 CONFIGURACIÓN DEL PADRÓN ELECTORAL
PADRON_POR_ESCUELA = {
    "Escuela Patria": 3500,
    "Escuela Centro": 2800,
    "Escuela Nacional": 4200,
    "Colegio San Ignacio": 1500,
    "Colegio Virgen de Loreto": 3100
}

# 🏫 ASIGNACIÓN DE MESAS POR ESCUELA
MESAS_POR_ESCUELA = {
    "Escuela Patria": [1240, 1241, 1242, 1243, 1244, 1245, 1246, 1247, 1248],
    "Escuela Centro": [1249, 1250, 1251, 1252, 1253, 1254, 1255, 1256],
    "Escuela Nacional": [1257, 1258, 1259, 1260, 1261, 1262, 1263, 1264],
    "Colegio San Ignacio": [1265, 1266, 1267, 1268, 1269, 1270, 1271, 1272],
    "Colegio Virgen de Loreto": [1273, 1274, 1275, 1276, 1277, 1278, 1279, 1280]
}

TOTAL_PADRON_GENERAL = sum(PADRON_POR_ESCUELA.values())

def obtener_escuela_por_mesa(numero_mesa):
    try:
        mesa_int = int(numero_mesa)
    except:
        return None
        
    for escuela, lista_mesas in MESAS_POR_ESCUELA.items():
        if mesa_int in lista_mesas:
            return escuela
            
    return None

# Máquina de estados en memoria para los fiscales
estados_usuarios = {}

def guardar_en_postgres(telefono, tipo, escuela_mesa="-", corte="-", votos="-", obs="-", escuela="-"):
    """Inserta una nueva fila directamente en la base de datos PostgreSQL de Render"""
    if not DATABASE_URL:
        print("🔴 Error: DATABASE_URL no configurada. No se pudo guardar.")
        return

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        query = """
            INSERT INTO reportes (fecha_hora, telefono, tipo_reporte, escuela_mesa, corte_horario, cantidad_votos, observaciones, escuela, mesa, votos)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
        """
        
        try:
            votos_int = int(votos)
        except:
            votos_int = None
            
        valores = (
            datetime.now(),
            telefono,
            tipo,
            str(escuela_mesa), 
            str(corte),
            str(votos),
            str(obs),
            str(escuela),      
            str(escuela_mesa), 
            votos_int          
        )
        
        cur.execute(query, valores)
        conn.commit()
        cur.close()
        conn.close()
        print("🟢 Registro guardado correctamente en PostgreSQL.", flush=True)
        
    except Exception as e:
        print(f"🔴 Error al guardar en PostgreSQL: {e}", flush=True)


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        telefono = request.values.get("From", "")
        mensaje_recibido = request.values.get("Body", "").strip().lower()
        
        print(f"📥 MENSAJE RECIBIDO - Teléfono: {telefono} | Mensaje: {mensaje_recibido}", flush=True)
        
        response = MessagingResponse()
        
        if telefono not in estados_usuarios or mensaje_recibido in ["hola", "buen dia", "buenas", "inicio", "reinicio"]:
            estados_usuarios[telefono] = {"estado": "MENU_PRINCIPAL", "datos": {}}
            msg = (
                "¡Hola! Bienvenido al Sistema de Monitoreo Electoral 🗳️.\n\n"
                "Por favor, selecciona una opción enviando el número:\n"
                "1️⃣ Votantes hasta el momento\n"
                "2️⃣ Otro motivo (Reportar incidente / Aviso)"
            )
            response.message(msg)
            print("📤 Respondiendo con Menú Principal", flush=True)
            return Response(str(response), mimetype='text/xml')

        estado_actual = estados_usuarios[telefono]["estado"]
        print(f"🔄 Estado actual: {estado_actual}", flush=True)

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
                response.message("Por favor, escribe detalladamente el motivo de tu aviso o el incidente que deseas reportar:")
            else:
                response.message("Opción inválida. Por favor, envía *1* o *2*.")

        elif estado_actual == "ESPERANDO_INCIDENTE":
            guardar_en_postgres(telefono=telefono, tipo="INCIDENTE", obs=request.values.get("Body"))
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
            texto_mesa = request.values.get("Body").strip()
            numeros_encontrados = re.findall(r'\d+', texto_mesa)
            
            if numeros_encontrados:
                numero_mesa = numeros_encontrados[0]
                print(f"🔎 Buscando escuela para la mesa: {numero_mesa}", flush=True)
                escuela_detectada = obtener_escuela_por_mesa(numero_mesa)
                
                if escuela_detectada:
                    print(f"🏫 Escuela detectada: {escuela_detectada}", flush=True)
                    estados_usuarios[telefono]["datos"]["mesa"] = numero_mesa
                    estados_usuarios[telefono]["datos"]["escuela"] = escuela_detectada
                    estados_usuarios[telefono]["estado"] = "ESPERANDO_VOTOS"
                    response.message(f"📍 Detectado: *{escuela_detectada}* (Mesa {numero_mesa}).\n\nFinalmente, ingresa la *Cantidad Total de Votantes* acumulados hasta este horario (solo números):")
                else:
                    print(f"⚠️ Mesa {numero_mesa} no encontrada.", flush=True)
                    response.message(f"⚠️ El número de mesa *{numero_mesa}* no está asignado a ninguna escuela del padrón. Por favor, verifícalo e ingrésalo nuevamente:")
            else:
                response.message("No logré identificar un número de mesa válido. Por favor, ingresa el número (ej: 45):")

        elif estado_actual == "ESPERANDO_VOTOS":
            if mensaje_recibido.isdigit():
                votos = int(mensaje_recibido)
                datos_fiscal = estados_usuarios[telefono]["datos"]
                
                print(f"💾 Guardando votos. Mesa: {datos_fiscal['mesa']}, Escuela: {datos_fiscal['escuela']}, Votos: {votos}", flush=True)
                
                guardar_en_postgres(
                    telefono=telefono,
                    tipo="VOTOS_CORTE",
                    escuela_mesa=datos_fiscal["mesa"],
                    corte=datos_fiscal["horario"],
                    votos=votos,
                    escuela=datos_fiscal["escuela"]
                )
                
                estados_usuarios[telefono] = {"estado": "MENU_PRINCIPAL", "datos": {}}
                response.message(f"✅ ¡Datos guardados con éxito!\n\n🏫 Escuela: {datos_fiscal['escuela']}\n🗳️ Mesa: {datos_fiscal['mesa']}\n⏰ Corte: {datos_fiscal['horario']}\n📊 Votos: {votos}\n\nMuchas gracias por tu reporte. Si deseas realizar otra acción, escribe 'Hola'.")
            else:
                response.message("Por favor, introduce una cantidad válida usando solo números enteros (ej: 142).")

        print("📤 Enviando XML a Twilio...", flush=True)
        return Response(str(response), mimetype='text/xml')

    except Exception as e:
        print(f"🔴 ERROR CRÍTICO EN WEBHOOK: {e}", flush=True)
        error_response = MessagingResponse()
        error_response.message(f"Hubo un error interno en el bot: {e}")
        return Response(str(error_response), mimetype='text/xml')


@app.route("/", methods=["GET"])
@requires_auth
def dashboard():
    reportes = []
    incidencias = []
    
    # LEEMOS DIRECTAMENTE DESDE POSTGRESQL (No desde Excel)
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        # 1. Obtener reportes de votos numéricos
        cur.execute("""
            SELECT fecha_hora, telefono, escuela, escuela_mesa, corte_horario, cantidad_votos 
            FROM reportes 
            WHERE tipo_reporte = 'VOTOS_CORTE'
            ORDER BY fecha_hora DESC;
        """)
        columnas_votos = ["Fecha y Hora", "Teléfono", "Escuela", "Mesa", "Corte Horario", "Votos"]
        for fila in cur.fetchall():
            # Convertimos la fecha a string prolijo
            fecha_str = fila[0].strftime("%d/%m/%Y %H:%M:%S") if fila[0] else ""
            reportes.append(dict(zip(columnas_votos, [fecha_str, fila[1], fila[2], fila[3], fila[4], fila[5]])))

        # 2. Obtener incidentes
        cur.execute("""
            SELECT fecha_hora, telefono, observaciones 
            FROM reportes 
            WHERE tipo_reporte = 'INCIDENTE'
            ORDER BY fecha_hora DESC;
        """)
        columnas_incidencias = ["Fecha y Hora", "Teléfono / Fiscal", "Mensaje / Alerta"]
        for fila in cur.fetchall():
            fecha_str = fila[0].strftime("%d/%m/%Y %H:%M:%S") if fila[0] else ""
            incidencias.append(dict(zip(columnas_incidencias, [fecha_str, fila[1], fila[2]])))
            
        cur.close()
        conn.close()
    except Exception as e:
        print(f"🔴 Error al leer datos de PostgreSQL para el Dashboard: {e}", flush=True)
            
    return render_template("dashboard.html", reportes=reportes, incidencias=incidencias)


# =========================================================
# VISTA DE PORCENTAJES EN TIEMPO REAL
# =========================================================
@app.route('/estadisticas')
@requires_auth
def mostrar_estadisticas():
    votos_actuales_escuela = {}
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        # Query optimizada para traer el último corte enviado por cada mesa
        query = """
            WITH ultimos_reportes AS (
                SELECT DISTINCT ON (escuela, mesa) escuela, mesa, votos
                FROM reportes
                WHERE votos IS NOT NULL
                ORDER BY escuela, mesa, fecha_hora DESC
            )
            SELECT escuela, SUM(votos) 
            FROM ultimos_reportes 
            GROUP BY escuela;
        """
        cur.execute(query)
        resultados = cur.fetchall()
        votos_actuales_escuela = {fila[0]: fila[1] for fila in resultados}
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Nota: No se pudieron cargar datos de la DB ({e}). Mostrando gráfico en cero.")

    tabla_escuelas = []
    total_votos_general = 0
    
    for escuela, padron in PADRON_POR_ESCUELA.items():
        votes = votos_actuales_escuela.get(escuela, 0)
        total_votos_general += votes
        porcentaje = (votes / padron) * 100 if padron > 0 else 0
        
        tabla_escuelas.append({
            "nombre": escuela,
            "votos": votes,
            "padron": padron,
            "porcentaje": round(porcentaje, 2)
        })
    
    porcentaje_general = (total_votos_general / TOTAL_PADRON_GENERAL) * 100 if TOTAL_PADRON_GENERAL > 0 else 0
    
    html_template = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Monitoreo Electoral - Porcentajes de Participación</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; margin: 0; padding: 20px; color: #333; }
            .container { max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
            h1 { text-align: center; color: #1e3a8a; margin-bottom: 30px; }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; }
            th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #ddd; }
            th { background-color: #1e3a8a; color: white; }
            tr:hover { background-color: #f1f5f9; }
            .total-row { font-weight: bold; background-color: #e2e8f0; }
            .progress-bar-bg { background-color: #e2e8f0; border-radius: 8px; width: 100px; height: 12px; display: inline-block; vertical-align: middle; margin-right: 8px; overflow: hidden; }
            .progress-bar-fill { background-color: #3b82f6; height: 100%; border-radius: 8px; }
            .porcentaje-texto { font-weight: bold; color: #1e3a8a; display: inline-block; width: 50px; text-align: right; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📊 Participación Electoral en Tiempo Real</h1>
            <table>
                <thead>
                    <tr>
                        <th>Escuela</th>
                        <th>Votaron</th>
                        <th>Padrón Total</th>
                        <th>Porcentaje de Avance</th>
                    </tr>
                </thead>
                <tbody>
                    {% for e in escuelas %}
                    <tr>
                        <td>{{ e.nombre }}</td>
                        <td>{{ e.votos }}</td>
                        <td>{{ e.padron }}</td>
                        <td>
                            <div class="progress-bar-bg"><div class="progress-bar-fill" style="width: {{ e.porcentaje }}%"></div></div>
                            <span class="porcentaje-texto">{{ e.porcentaje }}%</span>
                        </td>
                    </tr>
                    {% endfor %}
                    <tr class="total-row">
                        <td>TOTAL GENERAL</td>
                        <td>{{ total_votos }}</td>
                        <td>{{ total_padron }}</td>
                        <td>
                            <div class="progress-bar-bg"><div class="progress-bar-fill" style="width: {{ total_porcentaje }}%; background-color: #10b981;"></div></div>
                            <span class="porcentaje-texto">{{ total_porcentaje }}%</span>
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
    </body>
    </html>
    """
    
    return render_template_string(
        html_template, 
        escuelas=tabla_escuelas, 
        total_votos=total_votos_general, 
        total_padron=TOTAL_PADRON_GENERAL, 
        total_porcentaje=round(porcentaje_general, 2)
    )

@app.route('/limpiar-datos', methods=['POST'])
@requires_auth
def limpiar_datos():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        # Vaciamos la tabla de reportes
        cur.execute("TRUNCATE TABLE reportes RESTART IDENTITY CASCADE;")
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"status": "success", "message": "Base de datos limpiada con éxito"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    # Render asigna un puerto automáticamente en la variable de entorno PORT
    puerto = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=puerto)