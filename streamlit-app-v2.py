import json
import time
import threading
from dataclasses import dataclass

import streamlit as st
import paho.mqtt.client as mqtt

import requests
import pandas as pd
from streamlit_autorefresh import st_autorefresh


# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(page_title="ESP32 Dashboard", layout="wide")


# ============================================================
# CONFIG (Secrets)
# ============================================================
MQTT_HOST = st.secrets["mqtt"]["host"]
MQTT_PORT = int(st.secrets["mqtt"]["port"])
MQTT_USER = st.secrets["mqtt"].get("username", "")
MQTT_PASS = st.secrets["mqtt"].get("password", "")

# ThingSpeak (Node-RED publish field1..4 + status)
TS_CHANNEL_ID = str(st.secrets.get("thingspeak", {}).get("channel_id", "3207137"))
TS_READ_KEY = str(st.secrets.get("thingspeak", {}).get("read_api_key", ""))  # vide si public

# MQTT topics (ESP32 #2)
TOPIC_MOTOR_CMD = "ESP32/2 moteur"
TOPIC_SERVO_CMD = "ESP32/2 servo"
TOPIC_LEDRGB_CMD = "ESP32/2 Led RGB"
TOPIC_STATUS = "esp32_2/status"

# MQTT topics (Node #1 sensors)
TOPIC_NODE1_DATA = "esp32/data"
TOPIC_NODE1_TEMP = "esp32/temp"
TOPIC_NODE1_HUM = "esp32/humidity"
TOPIC_NODE1_FL = "esp32/flame"
TOPIC_NODE1_LDR = "esp32/ldr"


# ============================================================
# THRESHOLDS
# ============================================================
FLAME_THRESHOLD = 2000
TEMP_MEDIUM = 35.0
TEMP_HIGH = 45.0

REFRESH_MS = 2000
TS_CACHE_TTL_S = 20
AUTO_STOP_COOLDOWN_S = 10


# ============================================================
# HELPERS
# ============================================================
def compute_levels(temp, flame):
    reasons = []
    level = "ok"

    try:
        if flame is not None and int(flame) < FLAME_THRESHOLD:
            level = "bad"
            reasons.append(f"Flamme détectée (ADC {int(flame)} < {FLAME_THRESHOLD})")
    except Exception:
        pass

    try:
        if temp is not None and float(temp) >= TEMP_HIGH:
            level = "bad"
            reasons.append(f"Temp élevée ({float(temp):.1f}°C ≥ {TEMP_HIGH}°C)")
        elif level != "bad" and temp is not None and TEMP_MEDIUM <= float(temp) < TEMP_HIGH:
            level = "warn"
            reasons.append(f"Temp moyenne ({float(temp):.1f}°C)")
    except Exception:
        pass

    return level, " / ".join(reasons) if reasons else "Rien à signaler"


def show_level_box(level, text):
    if level == "ok":
        st.success(text)
    elif level == "warn":
        st.warning(text)
    else:
        st.error(text)


def fmt(v, nd=1):
    if v is None:
        return "—"
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return "—"


def to_int(v):
    if v is None:
        return None
    try:
        return int(v)
    except Exception:
        return None


def clamp01(x):
    return max(0.0, min(1.0, float(x)))


def progress_from_range(value, vmin, vmax):
    if value is None:
        return 0.0
    try:
        return clamp01((float(value) - vmin) / (vmax - vmin))
    except Exception:
        return 0.0


def age(now_ts, ts):
    if not ts:
        return "—"
    return f"{int(now_ts - ts)}s"


# ============================================================
# MQTT Manager (thread-safe)
# ============================================================
@dataclass
class MqttState:
    connected: bool = False
    last_status: dict | None = None
    last_node1: dict | None = None
    last_seen_topic: str = ""
    last_seen_payload: str = ""
    ts_last_any: float | None = None
    ts_last_status: float | None = None
    ts_last_node1: float | None = None


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
        self.client.connect(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def publish(self, topic: str, payload: str):
        self.client.publish(topic, payload)

    def snapshot(self) -> MqttState:
        with self._lock:
            return MqttState(
                connected=self.state.connected,
                last_status=None if self.state.last_status is None else dict(self.state.last_status),
                last_node1=None if self.state.last_node1 is None else dict(self.state.last_node1),
                last_seen_topic=self.state.last_seen_topic,
                last_seen_payload=self.state.last_seen_payload,
                ts_last_any=self.state.ts_last_any,
                ts_last_status=self.state.ts_last_status,
                ts_last_node1=self.state.ts_last_node1,
            )

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        with self._lock:
            self.state.connected = True

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
        now_ts = time.time()

        with self._lock:
            self.state.last_seen_topic = topic
            self.state.last_seen_payload = payload
            self.state.ts_last_any = now_ts

        if topic == TOPIC_STATUS:
            try:
                data = json.loads(payload)
                with self._lock:
                    self.state.last_status = data
                    self.state.ts_last_status = now_ts
            except Exception:
                pass
            return

        if topic == TOPIC_NODE1_DATA:
            try:
                data = json.loads(payload)
                with self._lock:
                    self.state.last_node1 = data
                    self.state.ts_last_node1 = now_ts
            except Exception:
                pass
            return

        # fallback si Node1 publie par topics séparés
        if topic in [TOPIC_NODE1_TEMP, TOPIC_NODE1_HUM, TOPIC_NODE1_FL, TOPIC_NODE1_LDR]:
            with self._lock:
                if self.state.last_node1 is None:
                    self.state.last_node1 = {}
                key_map = {
                    TOPIC_NODE1_TEMP: "temperature",
                    TOPIC_NODE1_HUM: "humidity",
                    TOPIC_NODE1_FL: "flame",
                    TOPIC_NODE1_LDR: "ldr",
                }
                key = key_map.get(topic)
                if key:
                    try:
                        if key in ["temperature", "humidity"]:
                            self.state.last_node1[key] = float(payload)
                        else:
                            self.state.last_node1[key] = int(payload)
                    except Exception:
                        self.state.last_node1[key] = payload
                self.state.ts_last_node1 = now_ts


@st.cache_resource
def get_mqtt_manager():
    m = MqttManager(MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS)
    m.start()
    return m


mqtt_mgr = get_mqtt_manager()


# ============================================================
# ThingSpeak reader (cache)
# Node-RED : field1=temp field2=humidity field3=flame field4=ldr status=ESP32_Data
# ============================================================
@st.cache_data(ttl=TS_CACHE_TTL_S)
def fetch_thingspeak_df(channel_id: str, read_key: str, results: int = 180) -> pd.DataFrame:
    url = f"https://api.thingspeak.com/channels/{channel_id}/feeds.json"
    params = {"results": results}
    if read_key:
        params["api_key"] = read_key

    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()

    rows = []
    for f in data.get("feeds", []):
        rows.append(
            {
                "created_at": f.get("created_at"),
                "temp": f.get("field1"),
                "humidity": f.get("field2"),
                "flame": f.get("field3"),
                "ldr": f.get("field4"),
                "status": f.get("status"),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    for c in ["temp", "humidity", "flame", "ldr"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["created_at"]).sort_values("created_at")
    return df


# ============================================================
# Refresh UI (2s)
# ============================================================
st_autorefresh(interval=REFRESH_MS, key="refresh_2s")


# ============================================================
# SESSION STATE
# ============================================================
if "last_auto_stop_ts" not in st.session_state:
    st.session_state.last_auto_stop_ts = 0.0


# ============================================================
# HEADER
# ============================================================
st.title("Dashboard ESP32 (MQTT + ThingSpeak)")
st.caption("Refresh UI 2s • ThingSpeak cache 20s • Seuils sécurité + jauges")


# ============================================================
# DATA SNAPSHOT
# ============================================================
snap = mqtt_mgr.snapshot()
now_ts = time.time()

# Node1 from MQTT
temp_mqtt = hum_mqtt = flame_mqtt = ldr_mqtt = None
alerte = None
if snap.last_node1:
    temp_mqtt = snap.last_node1.get("temperature")
    hum_mqtt = snap.last_node1.get("humidity")
    flame_mqtt = snap.last_node1.get("flame")
    ldr_mqtt = snap.last_node1.get("ldr")
    alerte = snap.last_node1.get("alerte")

# ESP32 #2 status
motors_state = servo_angle = led_state = None
if snap.last_status:
    motors_state = str(snap.last_status.get("motors", "")).upper()
    servo_angle = snap.last_status.get("servo_angle")
    led_state = str(snap.last_status.get("led", "")).upper()


# ============================================================
# 1) CONNEXION + LAST MSG + STATUS ESP32#2
# ============================================================
st.subheader("Connexion / MQTT")
a, b, c = st.columns([1, 1, 2])

with a:
    st.write("Broker:", f"{MQTT_HOST}:{MQTT_PORT}")
    show_level_box("ok" if snap.connected else "bad", "🟢 Connecté" if snap.connected else "🔴 Déconnecté")
    st.write("Âge dernier RX:", age(now_ts, snap.ts_last_any))

with b:
    st.write("Dernier message MQTT:")
    st.code(f"{snap.last_seen_topic}\n{snap.last_seen_payload}" if snap.last_seen_topic else "—")

with c:
    st.write("État ESP32 #2 (esp32_2/status):")
    st.json(snap.last_status if snap.last_status else {})

st.divider()


# ============================================================
# 2) CAPTEURS MQTT (Node #1) + KPI + Jauges
# ============================================================
st.subheader("Capteurs Node #1 (MQTT) + Sécurité")

lvl, reason = compute_levels(temp_mqtt, flame_mqtt)
show_level_box(lvl, f"Sécurité: {reason}")

st.info(
    f"Seuils: flamme < {FLAME_THRESHOLD} = DANGER | temp ≥ {TEMP_MEDIUM}°C = ATTENTION | temp ≥ {TEMP_HIGH}°C = DANGER"
)

if alerte is not None:
    st.info(f"Alerte Node1: {alerte}")

# KPI blocks
c1, c2, c3, c4 = st.columns(4)

temp_level = "bad" if (temp_mqtt is not None and float(temp_mqtt) >= TEMP_HIGH) else "warn" if (
    temp_mqtt is not None and float(temp_mqtt) >= TEMP_MEDIUM
) else "ok"
flame_level = "bad" if (flame_mqtt is not None and int(flame_mqtt) < FLAME_THRESHOLD) else "ok"

with c1:
    st.metric("Température (MQTT)", f"{fmt(temp_mqtt, 1)} °C")
    show_level_box(temp_level, "Température OK" if temp_level == "ok" else "Température attention" if temp_level == "warn" else "Température DANGER")
    st.progress(progress_from_range(temp_mqtt, 0, 60))

with c2:
    st.metric("Humidité (MQTT)", f"{fmt(hum_mqtt, 0)} %")
    st.progress(progress_from_range(hum_mqtt, 0, 100))

with c3:
    fval = to_int(flame_mqtt)
    st.metric("Flamme (MQTT ADC)", "—" if fval is None else str(fval))
    show_level_box(flame_level, "Flamme OK" if flame_level == "ok" else "FLAMME DANGER")
    st.progress(progress_from_range(fval, 0, 4095))

with c4:
    lval = to_int(ldr_mqtt)
    st.metric("LDR (MQTT ADC)", "—" if lval is None else str(lval))
    st.progress(progress_from_range(lval, 0, 4095))

st.divider()


# ============================================================
# 3) FAIL-SAFE AUTO STOP
# ============================================================
st.subheader("Fail-safe (Auto STOP moteurs)")

auto_stop = st.toggle("Activer Auto STOP si danger", value=True)
st.caption(f"Cooldown anti-spam: {AUTO_STOP_COOLDOWN_S}s")

motors_on = (motors_state == "ON")

btn1, btn2 = st.columns(2)
with btn1:
    if st.button("🛑 STOP moteurs maintenant", use_container_width=True):
        mqtt_mgr.publish(TOPIC_MOTOR_CMD, "OFF")
        st.success("STOP moteurs envoyé")
with btn2:
    if st.button("✅ ACK alerte", use_container_width=True):
        st.success("Alerte acquittée (si danger persiste, auto-stop peut se relancer).")

if auto_stop and lvl == "bad" and motors_on:
    now2 = time.time()
    if now2 - st.session_state.last_auto_stop_ts > AUTO_STOP_COOLDOWN_S:
        mqtt_mgr.publish(TOPIC_MOTOR_CMD, "OFF")
        st.session_state.last_auto_stop_ts = now2
        st.warning("FAIL-SAFE: STOP moteurs envoyé (danger détecté)")

st.divider()


# ============================================================
# 4) CONTROLES MQTT (ESP32 #2)
# ============================================================
st.subheader("Contrôles (MQTT → ESP32 #2)")

left, right = st.columns(2)

with left:
    st.markdown("### Moteurs")
    b1, b2 = st.columns(2)
    with b1:
        if st.button("🟢 START moteurs", use_container_width=True):
            mqtt_mgr.publish(TOPIC_MOTOR_CMD, "ON")
            st.success("Commande envoyée: moteurs ON")
    with b2:
        if st.button("🛑 STOP moteurs", use_container_width=True):
            mqtt_mgr.publish(TOPIC_MOTOR_CMD, "OFF")
            st.success("Commande envoyée: moteurs OFF")

    st.markdown("### Servo")
    angle = st.slider("Angle servo (0–180)", 0, 180, 90, 1)
    b3, b4 = st.columns(2)
    with b3:
        if st.button("Envoyer angle servo", use_container_width=True):
            mqtt_mgr.publish(TOPIC_SERVO_CMD, str(angle))
            st.success(f"Servo -> {angle}°")
    with b4:
        if st.button("Bouger servo (180→90)", use_container_width=True):
            mqtt_mgr.publish(TOPIC_SERVO_CMD, "180")
            time.sleep(0.25)
            mqtt_mgr.publish(TOPIC_SERVO_CMD, "90")
            st.success("Servo: 180° -> 90°")

with right:
    st.markdown("### LED RGB")
    mode_rgb = st.radio(
        "Mode LED RGB",
        ["ON/OFF (firmware actuel)", "RGB (JSON) (si firmware modifié)"],
        horizontal=True
    )

    if mode_rgb.startswith("ON/OFF"):
        on = st.toggle("LED RGB ON", value=False)
        if st.button("Envoyer LED RGB", use_container_width=True):
            mqtt_mgr.publish(TOPIC_LEDRGB_CMD, "ON" if on else "OFF")
            st.success(f"LED RGB -> {'ON' if on else 'OFF'}")
    else:
        r = st.slider("R", 0, 255, 255, 1)
        g = st.slider("G", 0, 255, 255, 1)
        b = st.slider("B", 0, 255, 255, 1)
        if st.button("Envoyer couleur RGB", use_container_width=True):
            payload = json.dumps({"r": r, "g": g, "b": b})
            mqtt_mgr.publish(TOPIC_LEDRGB_CMD, payload)
            st.success(f"LED RGB -> {payload}")

st.divider()

# ============================================================
# 5) DEBUG (optionnel)
# ============================================================
with st.expander("Debug (MQTT)"):
    st.write("Last RX age:", age(now_ts, snap.ts_last_any))
    st.code(f"{snap.last_seen_topic}\n{snap.last_seen_payload}" if snap.last_seen_topic else "—")
    d1, d2 = st.columns(2)
    with d1:
        st.write("esp32_2/status")
        st.json(snap.last_status if snap.last_status else {})
    with d2:
        st.write("Node1 (esp32/data)")
        st.json(snap.last_node1 if snap.last_node1 else {})

st.caption("Note: UI refresh 2s. ThingSpeak lu via HTTP (cache 20s). Recommande Node-RED ≥15s/msg.")
