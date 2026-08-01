import os
import threading
import time
import requests
from flask import Flask, jsonify, render_template, request
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, timezone

# Definir la zona horaria de Honduras (UTC-6)
HONDURAS_TZ = timezone(timedelta(hours=-6))

app = Flask(__name__)

# Configuración segura de la base de datos (PostgreSQL en Render u otra URI)
database_url = os.environ.get("DATABASE_URL", "sqlite:///telemetria.db")
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# Configuración de la API de Resend (https://resend.com) para el envío de
# correos. Se usa una API HTTP en vez de SMTP porque Render bloquea el
# tráfico saliente por los puertos SMTP (25, 465, 587) en su plan gratuito;
# la API de Resend viaja por HTTPS (puerto 443), que nunca está bloqueado.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_API_URL = "https://api.resend.com/emails"
# Mientras no se verifique un dominio propio en Resend, el remitente debe
# ser este dominio de pruebas (sandbox) que Resend provee gratis.
EMAIL_ORIGEN = os.environ.get("EMAIL_ORIGEN", "Alertas SCADA <onboarding@resend.dev>")
EMAIL_DESTINO = os.environ.get("EMAIL_DESTINO", "classuth@gmail.com")

# Diccionario para registrar el último momento en que se envió un correo por
# categoría. Claves de combustible: "combustible_bajo" (<=20%),
# "combustible_cerca_lleno" (90%-98.99%), "combustible_derrame" (>=99%).
# Las claves "combinado_bajo", "combinado_cerca_lleno" y "combinado_derrame"
# controlan el caso en que la alerta de combustible correspondiente coincide
# con la de temperatura en el mismo dato recibido, para enviar un solo correo.
ultimo_envio_correo = {
    "temperatura": 0,
    "combustible_bajo": 0,
    "combustible_cerca_lleno": 0,
    "combustible_derrame": 0,
    "combinado_bajo": 0,
    "combinado_cerca_lleno": 0,
    "combinado_derrame": 0,
}
TIEMPO_ESPERA_CORREO = 60  # 60 segundos de espera entre cada correo idéntico

# Umbrales de alerta de combustible
UMBRAL_COMBUSTIBLE_BAJO = 20.0
UMBRAL_COMBUSTIBLE_CERCA_LLENO = 90.0
UMBRAL_COMBUSTIBLE_DERRAME = 99.0


def obtener_estado_combustible(combustible):
    """Devuelve el tipo de alerta de combustible según el porcentaje actual,
    o None si el nivel está en rango normal (sin alerta).
    """
    valor = float(combustible)
    if valor >= UMBRAL_COMBUSTIBLE_DERRAME:
        return "derrame"
    if valor >= UMBRAL_COMBUSTIBLE_CERCA_LLENO:
        return "cerca_lleno"
    if valor <= UMBRAL_COMBUSTIBLE_BAJO:
        return "bajo"
    return None


# Modelo adaptado exactamente a tu tabla existente "registros"
class Registro(db.Model):
    __tablename__ = "registros"

    id = db.Column(db.Integer, primary_key=True)
    generador_id = db.Column(db.Integer, nullable=False, default=1)
    temperatura = db.Column(db.Float, nullable=False)
    nivel_combustible = db.Column(db.Float, nullable=False)
    fecha = db.Column(db.String(20), nullable=False)
    hora = db.Column(db.String(20), nullable=False)


with app.app_context():
    db.create_all()


def _ejecutar_envio_correo(tipo_alerta, valores, origen, destino):
    """Función interna que se ejecuta en segundo plano para no congelar Flask.

    tipo_alerta puede ser: "temperatura", "combustible_bajo",
    "combustible_cerca_lleno", "combustible_derrame", o su combinación con
    temperatura: "combinado_bajo", "combinado_cerca_lleno", "combinado_derrame".
    valores es un diccionario que puede contener "combustible" y/o "temperatura",
    según el tipo de alerta, para armar el cuerpo del correo con los valores
    tal cual se están enviando en ese momento.

    El envío se hace por la API HTTP de Resend en vez de SMTP, porque Render
    bloquea el tráfico saliente por los puertos SMTP en su plan gratuito.
    """
    try:
        if tipo_alerta == "combustible_bajo":
            asunto = "🚨 ALERTA CRÍTICA: Nivel Muy Bajo de Combustible"
            cuerpo = (
                "Atención Administrador,\n\nEl sistema SCADA ha detectado un nivel muy"
                f" bajo de combustible en el Generador Principal.\nPorcentaje"
                f" actual: {valores.get('combustible')}%\n\nPor favor, proceda a abastecer el depósito"
                " inmediatamente."
            )
        elif tipo_alerta == "combustible_cerca_lleno":
            asunto = "⛽ ALERTA: Tanque de Combustible Cerca de su Límite Máximo"
            cuerpo = (
                "Atención Administrador,\n\nEl sistema SCADA ha detectado que el tanque"
                " de combustible del Generador Principal está próximo a llenarse por"
                f" completo.\nPorcentaje actual: {valores.get('combustible')}%\n\nSe recomienda"
                " detener el abastecimiento pronto para evitar un derrame."
            )
        elif tipo_alerta == "combustible_derrame":
            asunto = "🚨 ALERTA CRÍTICA: Posible Derrame de Combustible"
            cuerpo = (
                "Atención Administrador,\n\nEl sistema SCADA ha detectado que el tanque"
                " de combustible del Generador Principal está prácticamente lleno al"
                f" límite.\nPorcentaje actual: {valores.get('combustible')}%\n\nExiste riesgo"
                " de derrame. Por favor, detenga el abastecimiento de inmediato y"
                " verifique el tanque."
            )
        elif tipo_alerta == "temperatura":
            asunto = "🔥 ALERTA CRÍTICA: Sobrecalentamiento de Motor"
            cuerpo = (
                "Atención Administrador,\n\nEl sistema SCADA ha detectado una"
                " condición de sobrecalentamiento en el Motor"
                f" Cummins.\nTemperatura actual: {valores.get('temperatura')}°C\n\nVerifique el sistema"
                " de enfriamiento de inmediato."
            )
        elif tipo_alerta == "combinado_bajo":
            asunto = "🚨🔥 ALERTA CRÍTICA: Combustible Bajo y Sobrecalentamiento de Motor"
            cuerpo = (
                "Atención Administrador,\n\nEl sistema SCADA ha detectado DOS condiciones"
                " críticas de manera simultánea en el Generador Principal:\n\n"
                f"- Nivel de combustible actual: {valores.get('combustible')}%\n"
                f"- Temperatura actual del motor: {valores.get('temperatura')}°C\n\n"
                "Por favor, proceda a abastecer el depósito y verificar el sistema de"
                " enfriamiento de inmediato."
            )
        elif tipo_alerta == "combinado_cerca_lleno":
            asunto = "🚨🔥 ALERTA CRÍTICA: Tanque Cerca de Llenarse y Sobrecalentamiento de Motor"
            cuerpo = (
                "Atención Administrador,\n\nEl sistema SCADA ha detectado DOS condiciones"
                " críticas de manera simultánea en el Generador Principal:\n\n"
                f"- Nivel de combustible actual: {valores.get('combustible')}% (cerca de llenarse)\n"
                f"- Temperatura actual del motor: {valores.get('temperatura')}°C\n\n"
                "Por favor, detenga pronto el abastecimiento y verifique el sistema de"
                " enfriamiento de inmediato."
            )
        elif tipo_alerta == "combinado_derrame":
            asunto = "🚨🔥 ALERTA CRÍTICA: Posible Derrame de Combustible y Sobrecalentamiento de Motor"
            cuerpo = (
                "Atención Administrador,\n\nEl sistema SCADA ha detectado DOS condiciones"
                " críticas de manera simultánea en el Generador Principal:\n\n"
                f"- Nivel de combustible actual: {valores.get('combustible')}% (posible derrame)\n"
                f"- Temperatura actual del motor: {valores.get('temperatura')}°C\n\n"
                "Por favor, detenga el abastecimiento de inmediato y verifique el sistema de"
                " enfriamiento."
            )
        else:
            print(f"Tipo de alerta desconocido: {tipo_alerta}")
            return

        if not RESEND_API_KEY:
            print(
                "Error al enviar el correo en segundo plano"
                f" ({tipo_alerta}): falta configurar la variable de entorno"
                " RESEND_API_KEY en Render."
            )
            return

        payload = {
            "from": origen,
            "to": [destino],
            "subject": asunto,
            "text": cuerpo,
        }
        headers = {
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        }

        respuesta = requests.post(
            RESEND_API_URL, json=payload, headers=headers, timeout=20
        )

        if respuesta.status_code in (200, 201):
            print(f"Correo de alerta ({tipo_alerta}) enviado exitosamente en segundo plano.")
        else:
            print(
                f"Error al enviar el correo en segundo plano ({tipo_alerta}):"
                f" status {respuesta.status_code} - {respuesta.text}"
            )
    except Exception as e:
        print(f"Error al enviar el correo en segundo plano ({tipo_alerta}): {e}")


def enviar_alerta_correo(tipo_alerta, valores):
    """Controla el envío de correos respetando el cooldown de 60 segundos
    para cada tipo de alerta de forma independiente.

    Si tipo_alerta empieza con "combinado_" (por ejemplo "combinado_bajo"),
    se actualizan también los cooldowns individuales del tipo de combustible
    correspondiente ("combustible_bajo") y de "temperatura", para evitar que,
    apenas enviado el correo combinado, se dispare inmediatamente otro correo
    individual para el mismo dato ya reportado.
    """
    tiempo_actual = time.time()

    if tipo_alerta.startswith("combinado_"):
        tipo_combustible = tipo_alerta.replace("combinado_", "combustible_", 1)
        if tiempo_actual - ultimo_envio_correo[tipo_alerta] < TIEMPO_ESPERA_CORREO:
            return
        ultimo_envio_correo[tipo_alerta] = tiempo_actual
        ultimo_envio_correo[tipo_combustible] = tiempo_actual
        ultimo_envio_correo["temperatura"] = tiempo_actual
    else:
        if tiempo_actual - ultimo_envio_correo[tipo_alerta] < TIEMPO_ESPERA_CORREO:
            return
        ultimo_envio_correo[tipo_alerta] = tiempo_actual

    # Lanzar el envío de correo en un hilo separado (Evita el bloqueo de Flask y el Timeout)
    hilo = threading.Thread(
        target=_ejecutar_envio_correo,
        args=(
            tipo_alerta,
            valores,
            EMAIL_ORIGEN,
            EMAIL_DESTINO,
        ),
    )
    hilo.daemon = True
    hilo.start()


@app.route("/api/datos", methods=["POST"])
def recibir_datos():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No se recibieron datos JSON"}), 400

    temperatura = data.get("temperatura")
    combustible = data.get("nivel_combustible")
    generador_id = data.get("generador_id", 1)
    
    ahora_hn = datetime.now(HONDURAS_TZ)
    fecha = data.get("fecha", ahora_hn.strftime("%Y-%m-%d"))
    hora = data.get("hora", ahora_hn.strftime("%H:%M:%S"))

    if temperatura is None or combustible is None:
        return (
            jsonify({"error": "Faltan parámetros de temperatura o combustible"}),
            400,
        )

    try:
        nuevo_registro = Registro(
            generador_id=int(generador_id),
            temperatura=float(temperatura),
            nivel_combustible=float(combustible),
            fecha=str(fecha),
            hora=str(hora),
        )
        db.session.add(nuevo_registro)
        db.session.commit()

        # Evaluar umbrales de alerta para combustible y temperatura
        estado_combustible = obtener_estado_combustible(combustible)
        condicion_temperatura = float(temperatura) > 37.0

        # --- LOG TEMPORAL DE DIAGNÓSTICO ---
        # Imprime el valor exacto recibido en cada request para confirmar el
        # comportamiento del sensor de combustible frente a los umbrales.
        # Se puede quitar este bloque una vez confirmado el diagnóstico.
        print(
            f"[DEBUG] combustible={combustible} (estado={estado_combustible}) |"
            f" temperatura={temperatura} (alerta={condicion_temperatura})"
        )

        # Si la alerta de combustible (cualquiera de sus tres tipos) y la de
        # temperatura ocurren en el mismo dato recibido, se envía un único
        # correo combinado. Si solo ocurre una, se envía el correo específico
        # de esa categoría.
        if estado_combustible and condicion_temperatura:
            enviar_alerta_correo(
                f"combinado_{estado_combustible}",
                {"combustible": combustible, "temperatura": temperatura},
            )
        elif estado_combustible:
            enviar_alerta_correo(
                f"combustible_{estado_combustible}", {"combustible": combustible}
            )
        elif condicion_temperatura:
            enviar_alerta_correo("temperatura", {"temperatura": temperatura})

        return (
            jsonify({
                "mensaje": "Datos recibidos y procesados correctamente",
                "status": 201,
            }),
            201,
        )
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/error", methods=["POST"])
def recibir_error():
    data = request.get_json()
    return jsonify({"mensaje": "Error registrado correctamente"}), 200


@app.route("/api/estado/<int:gen_id>")
def obtener_estado(gen_id):
    ultimo = (
        Registro.query.filter_by(generador_id=gen_id)
        .order_by(Registro.id.desc())
        .first()
    )
    if not ultimo:
        ultimo = Registro.query.order_by(Registro.id.desc()).first()

    if not ultimo:
        return jsonify({"error": "No hay datos disponibles"}), 404

    return jsonify({
        "temperatura": ultimo.temperatura,
        "nivel_combustible": ultimo.nivel_combustible,
        "conexion": "Estable",
    })


@app.route("/")
def index():
    ultimo = Registro.query.order_by(Registro.id.desc()).first()
    return render_template("index.html", telemetria=ultimo)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
