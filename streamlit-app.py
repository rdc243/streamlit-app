import json
import time
import threading
from dataclasses import dataclass
import streamlit as st
import paho.mqtt.client as mqtt

st.set_page_config(page_title="ESP32 Dashboard", layout="wide")


# =========================
# CONFIG (Secrets)
# =========================
MQTT_HOST = st.secrets["mqtt"]["host"]
MQTT_PORT = int(st.secrets["mqtt"]["port"])
MQTT_USER = st.secrets["mqtt"].get("username", "")
MQTT_PASS = st.secrets["mqtt"].get("password", "")

# Topics ESP32 #2 (d'après ton code)
TOPIC_MOTOR_CMD = "ESP32/2 moteur"
TOPIC_SERVO_CMD = "ESP32/2 servo"
TOPIC_LEDRGB_CMD = "ESP32/2 Led RGB"
TOPIC_STATUS    = "esp32_2/status"

# Optionnel (capteurs Node #1 si tu veux afficher)
TOPIC_NODE1_DATA = "esp32/data"
TOPIC_NODE1_TEMP = "esp32/temp"
TOPIC_NODE1_HUM  = "esp32/humidity"
TOPIC_NODE1_FL   = "esp32/flame"
TOPIC_NODE1_LDR  = "esp32/ldr"


# =========================
# MQTT Manager (thread-safe)
# =========================
@dataclass
class MqttState:
    connected: bool = False
    last_status: dict | None = None
    last_node1: dict | None = None
    last_seen_topic: str = ""
    last_seen_payload: str = ""

class MqttManager:
    def __init__(self, host, port, username="", password=""):
        self.host = host
        self.port = port
        self.username = username
        self.password = password

        self.state = MqttState()
        self._lock = threading.Lock()

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if username or password:
            self.client.username_pw_set(username, password)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        self.client.reconnect_delay_set(min_delay=1, max_delay=10)

    def start(self):
        # Connect + background network loop
        self.client.connect(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def stop(self):
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def publish(self, topic: str, payload: str):
        # paho publish est OK ici car loop_start() gère le réseau
        self.client.publish(topic, payload)

    def snapshot(self) -> MqttState:
        with self._lock:
            # retourne une copie simple
            return MqttState(
                connected=self.state.connected,
                last_status=None if self.state.last_status is None else dict(self.state.last_status),
                last_node1=None if self.state.last_node1 is None else dict(self.state.last_node1),
                last_seen_topic=self.state.last_seen_topic,
                last_seen_payload=self.state.last_seen_payload,
            )

    # ===== callbacks =====
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        with self._lock:
            self.state.connected = True

        # Abonnements
        client.subscribe(TOPIC_STATUS)
        client.subscribe(TOPIC_NODE1_DATA)
        client.subscribe(TOPIC_NODE1_TEMP)
        client.subscribe(TOPIC_NODE1_HUM)
        client.subscribe(TOPIC_NODE1_FL)
        client.subscribe(TOPIC_NODE1_LDR)

    def _on_disconnect(self, client, userdata, reason_code, properties=None):
        with self._lock:
            self.state.connected = False

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode("utf-8", errors="replace").strip()

        with self._lock:
            self.state.last_seen_topic = topic
            self.state.last_seen_payload = payload

        # Status ESP32 #2 = JSON {"motors":"ON/OFF","servo_angle":X,"led":"ON/OFF"}
        if topic == TOPIC_STATUS:
            try:
                data = json.loads(payload)
                with self._lock:
                    self.state.last_status = data
            except Exception:
                pass

        # Node1 data JSON (si envoyé)
        if topic == TOPIC_NODE1_DATA:
            try:
                data = json.loads(payload)
                with self._lock:
                    self.state.last_node1 = data
            except Exception:
                pass


@st.cache_resource
def get_mqtt_manager():
    m = MqttManager(MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS)
    m.start()
    return m


mqtt_mgr = get_mqtt_manager()


# =========================
# UI
# =========================
st.title("Dashboard ESP32 (MQTT)")

snap = mqtt_mgr.snapshot()

colA, colB, colC = st.columns([1, 1, 2])

with colA:
    st.subheader("Connexion")
    st.write("Broker :", MQTT_HOST, ":", MQTT_PORT)
    st.write("Statut :", "🟢 Connecté" if snap.connected else "🔴 Déconnecté")

with colB:
    st.subheader("Dernier message")
    st.code(f"{snap.last_seen_topic}\n{snap.last_seen_payload}" if snap.last_seen_topic else "—", language="text")

with colC:
    st.subheader("État ESP32 #2 (topic status)")
    if snap.last_status:
        st.json(snap.last_status)
    else:
        st.info("Pas encore de status reçu sur esp32_2/status")


st.divider()

left, right = st.columns([1, 1])

# =========================
# CONTROLES
# =========================
with left:
    st.subheader("Moteurs (ESP32 #2)")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🟢 START moteurs", use_container_width=True):
            mqtt_mgr.publish(TOPIC_MOTOR_CMD, "ON")
            st.toast("Commande envoyée: moteurs ON")
    with c2:
        if st.button("🛑 STOP moteurs", use_container_width=True):
            mqtt_mgr.publish(TOPIC_MOTOR_CMD, "OFF")
            st.toast("Commande envoyée: moteurs OFF")

    st.subheader("Servo (ESP32 #2)")

    angle = st.slider("Angle servo (0–180)", 0, 180, 90, 1)
    c3, c4 = st.columns(2)
    with c3:
        if st.button("Envoyer angle", use_container_width=True):
            mqtt_mgr.publish(TOPIC_SERVO_CMD, str(angle))
            st.toast(f"Servo -> {angle}°")
    with c4:
        if st.button("Bouger (petit mouvement)", use_container_width=True):
            # petit mouvement sans modifier ton firmware
            mqtt_mgr.publish(TOPIC_SERVO_CMD, "180")
            time.sleep(0.25)
            mqtt_mgr.publish(TOPIC_SERVO_CMD, "90")
            st.toast("Servo: 180° -> 90°")

with right:
    st.subheader("LED RGB (ESP32 #2)")

    mode_rgb = st.radio(
        "Mode de contrôle",
        ["ON/OFF (compatible avec ton code actuel)", "RGB (JSON) (recommandé)"],
        horizontal=True
    )

    if mode_rgb.startswith("ON/OFF"):
        on = st.toggle("LED RGB ON", value=False)
        if st.button("Envoyer", use_container_width=True):
            mqtt_mgr.publish(TOPIC_LEDRGB_CMD, "ON" if on else "OFF")
            st.toast(f"LED RGB -> {'ON' if on else 'OFF'}")

    else:
        r = st.slider("R", 0, 255, 255, 1)
        g = st.slider("G", 0, 255, 255, 1)
        b = st.slider("B", 0, 255, 255, 1)
        if st.button("Envoyer couleur", use_container_width=True):
            payload = json.dumps({"r": r, "g": g, "b": b})
            mqtt_mgr.publish(TOPIC_LEDRGB_CMD, payload)
            st.toast(f"LED RGB -> {payload}")

    st.subheader("Capteurs (Node #1) (optionnel)")
    snap = mqtt_mgr.snapshot()
    if snap.last_node1:
        st.json(snap.last_node1)
    else:
        st.caption("Aucune donnée JSON reçue sur esp32/data (si ton Node #1 publie).")


st.divider()
st.caption("Astuce: si tu ne vois rien, vérifie que le broker MQTT est accessible depuis Internet (Streamlit Cloud est externe).")
