import json
import time
import threading
from dataclasses import dataclass

import requests
import pandas as pd
import streamlit as st
import paho.mqtt.client as mqtt
from streamlit_autorefresh import st_autorefresh

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(page_title="INDU 4.0 | ESP32 Control Center", layout="wide")

# ============================================================
# FUTURISTIC CSS THEME (Dark + Neon)
# ============================================================
st.markdown(
    """
<style>
:root{
  --bg:#070A12;
  --panel:#0C1222;
  --panel2:#0A0F1E;
  --text:#EAF0FF;
  --muted:#9FB0FF;
  --ok:#27F2A5;
  --warn:#FFD166;
  --bad:#FF4D6D;
  --neon:#7C5CFF;
  --cyan:#00E5FF;
  --border: rgba(124,92,255,.35);
  --shadow: 0 0 0.6rem rgba(124,92,255,.15);
}

html, body, [data-testid="stAppViewContainer"]{
  background: radial-gradient(1200px 600px at 10% 10%, rgba(124,92,255,.12), transparent 60%),
              radial-gradient(1000px 500px at 80% 30%, rgba(0,229,255,.10), transparent 55%),
              radial-gradient(900px 450px at 60% 90%, rgba(39,242,165,.08), transparent 55%),
              var(--bg) !important;
  color: var(--text) !important;
}

[data-testid="stSidebar"]{
  background: linear-gradient(180deg, rgba(12,18,34,.95), rgba(10,15,30,.95)) !important;
  border-right: 1px solid rgba(124,92,255,.20);
}

h1,h2,h3{
  letter-spacing: .4px;
}

.small-muted { color: var(--muted); font-size: .9rem; }

.hr-neon{
  height:1px; border:0;
  background: linear-gradient(90deg, transparent, rgba(124,92,255,.7), transparent);
  margin: 0.6rem 0 1.0rem 0;
}

.card{
  background: linear-gradient(180deg, rgba(12,18,34,.82), rgba(10,15,30,.82));
  border: 1px solid var(--border);
  box-shadow: var(--shadow);
  border-radius: 18px;
  padding: 16px 16px;
}

.card-title{
  font-size: 0.9rem;
  color: var(--muted);
  margin-bottom: 6px;
}

.kpi{
  display:flex; align-items:flex-end; justify-content:space-between;
  gap: 12px;
}
.kpi-value{
  font-size: 1.6rem;
  font-weight: 700;
  line-height: 1.0;
}
.kpi-unit{
  font-size: .9rem;
  color: var(--muted);
  margin-left: 6px;
}
.badge{
  padding: 4px 10px;
  border-radius: 999px;
  font-size: .8rem;
  border: 1px solid rgba(255,255,255,.12);
}
.badge.ok{ background: rgba(39,242,165,.12); color: var(--ok); border-color: rgba(39,242,165,.35);}
.badge.warn{ background: rgba(255,209,102,.12); color: var(--warn); border-color: rgba(255,209,102,.35);}
.badge.bad{ background: rgba(255,77,109,.12); color: var(--bad); border-color: rgba(255,77,109,.35);}

.gauge-wrap{ margin-top: 8px; }
.gauge-bar{
  width:100%;
  height: 12px;
  background: rgba(255,255,255,.06);
  border-radius: 999px;
  overflow:hidden;
  border: 1px solid rgba(124,92,255,.18);
}
.gauge-fill{
  height:100%;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(0,229,255,.9), rgba(124,92,255,.9), rgba(39,242,165,.9));
  width: 0%;
}
.gauge-fill.ok{ background: linear-gradient(90deg, rgba(39,242,165,.95), rgba(0,229,255,.85)); }
.gauge-fill.warn{ background: linear-gradient(90deg, rgba(255,209,102,.95), rgba(124,92,255,.85)); }
.gauge-fill.bad{ background: linear-gradient(90deg, rgba(255,77,109,.95), rgba(124,92,255,.85)); }
.gauge-meta{
  display:flex; justify-content:space-between; gap: 12px;
  margin-top: 6px; color: var(--muted); font-size: .85rem;
}

.pill{
  display:inline-block;
  padding: 6px 10px;
  border-radius: 12px;
  background: rgba(124,92,255,.10);
  border: 1px solid rgba(124,92,255,.25);
  color: var(--text);
  font-size: .9rem;
}

</style>
""",
    unsafe_allow_html=True,
)

# ============================================================
# CONFIG (Secrets)
# ============================================================
MQTT_HOST = st.secrets["mqtt"]["host"]
MQTT_PORT = int(st.secrets["mqtt"]["port"])
MQTT_USER = st.secrets["mqtt"].get("username", "")
MQTT_PASS = st.secrets["mqtt"].get("password", "")

TS_CHANNEL_ID = str(st.secrets["thingspeak"]["channel_id"])
TS_READ_KEY = st.secrets["thingspeak"].get("read_api_key", "")

# Topics ESP32 #2
TOPIC_MOTOR_CMD = "ESP32/2 moteur"
TOPIC_SERVO_CMD = "ESP32/2 servo"
TOPIC_LEDRGB_CMD = "ESP32/2 Led RGB"
TOPIC_STATUS = "esp32_2/status"

# Topics Node #1
TOPIC_NODE1_DATA = "esp32/data"
TOPIC_NODE1_TEMP = "esp32/temp"
TOPIC_NODE1_HUM = "esp32/humidity"
TOPIC_NODE1_FL = "esp32/flame"
TOPIC_NODE1_LDR = "esp32/ldr"

# ============================================================
# THRESHOLDS (alignés avec ton code)
# ============================================================
FLAME_THRESHOLD = 2000
TEMP_MEDIUM = 35.0
TEMP_HIGH = 45.0

# refresh UI
REFRESH_MS = 2000

# ThingSpeak cache
TS_CACHE_TTL_S = 20  # évite de spammer l'API

# Fail-safe anti-spam
AUTO_STOP_COOLDOWN_S = 10

# ============================================================
# HELPERS UI
# ============================================================
def level_badge(level: str) -> str:
    if level == "ok":
        return '<span class="badge ok">OK</span>'
    if level == "warn":
        return '<span class="badge warn">ATTENTION</span>'
    return '<span class="badge bad">DANGER</span>'


def compute_levels(temp, flame):
    """
    Retourne (global_level, reason)
    - danger si flame < 2000 OU temp >= 45
    - attention si temp entre 35 et 45
    - ok sinon
    """
    reasons = []
    level = "ok"

    try:
        if flame is not None and int(flame) < FLAME_THRESHOLD:
            level = "bad"
            reasons.append(f"Flamme détectée (flame={int(flame)} < {FLAME_THRESHOLD})")
    except Exception:
        pass

    try:
        if temp is not None and float(temp) >= TEMP_HIGH:
            level = "bad"
            reasons.append(f"Température élevée (temp={float(temp):.1f}°C ≥ {TEMP_HIGH}°C)")
        elif level != "bad" and temp is not None and TEMP_MEDIUM <= float(temp) < TEMP_HIGH:
            level = "warn"
            reasons.append(f"Température moyenne (temp={float(temp):.1f}°C)")
    except Exception:
        pass

    return level, " / ".join(reasons) if reasons else "Rien à signaler"


def fmt(v, nd=1):
    if v is None:
        return "—"
    try:
        f = float(v)
        return f"{f:.{nd}f}"
    except Exception:
        return " knowing"


def kpi_card(title, value, unit="", level="ok"):
    st.markdown(
        f"""
<div class="card">
  <div class="card-title">{title}</div>
  <div class="kpi">
    <div>
      <span class="kpi-value">{value}</span><span class="kpi-unit">{unit}</span>
    </div>
    {level_badge(level)}
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def gauge_card(title, value_num, vmin, vmax, level="ok", left_label="", right_label=""):
    # normalize
    try:
        val = float(value_num)
        pct = 0.0 if vmax == vmin else (val - vmin) / (vmax - vmin)
        pct = max(0.0, min(1.0, pct))
        pct100 = int(pct * 100)
    except Exception:
        val = None
        pct100 = 0

    val_txt = "—" if val is None else f"{val:.0f}" if abs(vmax - vmin) > 10 else f"{val:.1f}"

    st.markdown(
        f"""
<div class="card">
  <div class="card-title">{title}</div>
  <div class="kpi" style="margin-bottom:8px;">
    <div><span class="kpi-value">{val_txt}</span></div>
    {level_badge(level)}
  </div>
  <div class="gauge-wrap">
    <div class="gauge-bar">
      <div class="gauge-fill {level}" style="width:{pct100}%"></div>
    </div>
    <div class="gauge-meta">
      <span>{left_label}</span>
      <span>{right_label}</span>
    </div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


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
        self.state = MqttState()
        self._lock = threading.Lock()

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if username or password:
            self.client.username_pw_set(username, password)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)

        self.client.connect(host, port, keepalive=30)
        self.client.loop_start()

    def publish(self, topic: str, payload: str):
        self.client.publish(topic, payload)

    def snapshot(self) -> MqttState:
        with self._lock:
            s = self.state
            return MqttState(
                connected=s.connected,
                last_status=None if s.last_status is None else dict(s.last_status),
                last_node1=None if s.last_node1 is None else dict(s.last_node1),
                last_seen_topic=s.last_seen_topic,
                last_seen_payload=s.last_seen_payload,
                ts_last_any=s.ts_last_any,
                ts_last_status=s.ts_last_status,
                ts_last_node1=s.ts_last_node1,
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
        now = time.time()

        with self._lock:
            self.state.last_seen_topic = topic
            self.state.last_seen_payload = payload
            self.state.ts_last_any = now

        if topic == TOPIC_STATUS:
            try:
                data = json.loads(payload)
                with self._lock:
                    self.state.last_status = data
                    self.state.ts_last_status = now
            except Exception:
                pass
            return

        if topic == TOPIC_NODE1_DATA:
            try:
                data = json.loads(payload)
                with self._lock:
                    self.state.last_node1 = data
                    self.state.ts_last_node1 = now
            except Exception:
                pass
            return

        # fallback : si Node1 publie en topics simples
        if topic in [TOPIC_NODE1_TEMP, TOPIC_NODE1_HUM, TOPIC_NODE1_FL, TOPIC_NODE1_LDR]:
            with self._lock:
                if self.state.last_node1 is None:
                    self.state.last_node1 = {}
                kmap = {
                    TOPIC_NODE1_TEMP: "temperature",
                    TOPIC_NODE1_HUM: "humidity",
                    TOPIC_NODE1_FL: "flame",
                    TOPIC_NODE1_LDR: "ldr",
                }
                key = kmap.get(topic)
                if key:
                    try:
                        self.state.last_node1[key] = float(payload) if key in ["temperature", "humidity"] else int(payload)
                    except Exception:
                        self.state.last_node1[key] = payload
                self.state.ts_last_node1 = now


@st.cache_resource
def get_mqtt_manager():
    return MqttManager(MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS)


mqtt_mgr = get_mqtt_manager()

# ============================================================
# ThingSpeak fetch
# ============================================================
@st.cache_data(ttl=TS_CACHE_TTL_S)
def fetch_thingspeak_df(channel_id: str, read_key: str, results: int = 120) -> pd.DataFrame:
    url = f"https://api.thingspeak.com/channels/{channel_id}/feeds.json"
    params = {"results": results}
    if read_key:
        params["api_key"] = read_key

    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()

    feeds = data.get("feeds", [])
    rows = []
    for f in feeds:
        rows.append(
            {
                "created_at": f.get("created_at"),
                "temp": f.get("field1"),
                "humidity": f.get("field2"),
                "flame": f.get("field3"),
                "ldr": f.get("field4"),
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
# Refresh UI 2s
# ============================================================
st_autorefresh(interval=REFRESH_MS, key="refresh_2s")

# ============================================================
# Sidebar navigation
# ============================================================
st.sidebar.markdown("### 🧠 INDU 4.0 Control Center")
st.sidebar.markdown('<div class="small-muted">ESP32 • MQTT • Node-RED • ThingSpeak</div>', unsafe_allow_html=True)
st.sidebar.markdown('<hr class="hr-neon" />', unsafe_allow_html=True)

page = st.sidebar.radio(
    "Navigation",
    ["Overview", "Controls", "ThingSpeak", "Safety", "Debug"],
    index=0,
)

st.sidebar.markdown('<hr class="hr-neon" />', unsafe_allow_html=True)
st.sidebar.markdown(
    f'<span class="pill">Broker: {MQTT_HOST}:{MQTT_PORT}</span>',
    unsafe_allow_html=True,
)

# ============================================================
# Snapshot MQTT
# ============================================================
snap = mqtt_mgr.snapshot()
now = time.time()

def age(ts):
    if not ts:
        return "—"
    return f"{int(now - ts)}s"

# Extract values from Node1 JSON
t = h = fl = ld = None
alerte = None
if snap.last_node1:
    t = snap.last_node1.get("temperature")
    h = snap.last_node1.get("humidity")
    fl = snap.last_node1.get("flame")
    ld = snap.last_node1.get("ldr")
    alerte = snap.last_node1.get("alerte")

# Extract status from ESP32 #2
motors = servo_angle = led = None
if snap.last_status:
    motors = str(snap.last_status.get("motors", "")).upper()
    servo_angle = snap.last_status.get("servo_angle")
    led = str(snap.last_status.get("led", "")).upper()

global_level, global_reason = compute_levels(t, fl)

# ============================================================
# HEADER
# ============================================================
st.markdown(
    """
# ⚡ ESP32 Futuristic Dashboard
<div class="small-muted">Refresh 2s • ThingSpeak cache 20s • Auto-color KPIs • Safety fail-safe</div>
<hr class="hr-neon" />
""",
    unsafe_allow_html=True,
)

# ============================================================
# PAGES
# ============================================================
if page == "Overview":
    # Status cards row
    c1, c2, c3, c4 = st.columns(4)

    mqtt_level = "ok" if snap.connected else "bad"
    kpi_card("MQTT Connection", "ONLINE" if snap.connected else "OFFLINE", "", mqtt_level)

    # last msg age
    stale = snap.ts_last_any is not None and (now - snap.ts_last_any) > 12
    rx_level = "bad" if stale else "ok"
    st.markdown(
        f"""
<div class="card">
  <div class="card-title">Last MQTT RX</div>
  <div class="kpi">
    <div><span class="kpi-value">{age(snap.ts_last_any)}</span></div>
    {level_badge(rx_level)}
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    # global safety
    st.markdown(
        f"""
<div class="card">
  <div class="card-title">System Safety</div>
  <div class="kpi">
    <div><span class="kpi-value">{global_reason}</span></div>
    {level_badge(global_level)}
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    # motors state
    motors_level = "ok" if motors == "ON" else "warn" if motors == "OFF" else "warn"
    kpi_card("Motors State", motors if motors else "—", "", motors_level)

    st.write("")

    # KPI row (sensor)
    k1, k2, k3, k4 = st.columns(4)
    # compute per-kpi levels
    temp_level = "bad" if (t is not None and float(t) >= TEMP_HIGH) else "warn" if (t is not None and float(t) >= TEMP_MEDIUM) else "ok"
    flame_level = "bad" if (fl is not None and int(fl) < FLAME_THRESHOLD) else "ok"

    with k1:
        kpi_card("Temperature", fmt(t, 1), "°C", temp_level)
    with k2:
        kpi_card("Humidity", fmt(h, 0), "%", "ok" if h is not None else "warn")
    with k3:
        kpi_card("Flame (ADC)", "—" if fl is None else str(int(fl)), "", flame_level)
    with k4:
        kpi_card("LDR (ADC)", "—" if ld is None else str(int(ld)), "", "ok" if ld is not None else "warn")

    # Gauges row
    g1, g2, g3, g4 = st.columns(4)
    with g1:
        # temp gauge 0..60
        gauge_card("Temp Gauge", t if t is not None else None, 0, 60, temp_level, "0°C", "60°C")
        st.caption(f"Seuils: ≥{TEMP_MEDIUM}°C = attention • ≥{TEMP_HIGH}°C = danger")
    with g2:
        # humidity 0..100
        gauge_card("Humidity Gauge", h if h is not None else None, 0, 100, "ok", "0%", "100%")
    with g3:
        # flame 0..4095 (danger when low)
        gauge_card("Flame Gauge", fl if fl is not None else None, 0, 4095, flame_level, "0", "4095")
        st.caption(f"Danger si flamme < {FLAME_THRESHOLD}")
    with g4:
        gauge_card("LDR Gauge", ld if ld is not None else None, 0, 4095, "ok" if ld is not None else "warn", "0", "4095")

    st.markdown('<hr class="hr-neon" />', unsafe_allow_html=True)

    # Compact status JSON cards
    left, right = st.columns(2)
    with left:
        st.subheader("ESP32 #2 Status (MQTT)")
        if snap.last_status:
            st.json(snap.last_status)
        else:
            st.info("Aucun status reçu sur esp32_2/status.")
    with right:
        st.subheader("Node #1 Sensors (MQTT)")
        if snap.last_node1:
            st.json(snap.last_node1)
        else:
            st.info("Aucune donnée reçue sur esp32/data (ou topics temp/humidity/flame/ldr).")


elif page == "Controls":
    st.subheader("🎛️ Controls (MQTT → ESP32 #2)")
    st.markdown('<div class="small-muted">Commandes temps réel : moteurs, servo, LED RGB.</div>', unsafe_allow_html=True)
    st.markdown('<hr class="hr-neon" />', unsafe_allow_html=True)

    left, right = st.columns([1, 1])

    with left:
        st.markdown("### 🧲 Motors")
        b1, b2 = st.columns(2)
        with b1:
            if st.button("🟢 START Motors", use_container_width=True):
                mqtt_mgr.publish(TOPIC_MOTOR_CMD, "ON")
                st.toast("Commande envoyée: moteurs ON")
        with b2:
            if st.button("🛑 STOP Motors", use_container_width=True):
                mqtt_mgr.publish(TOPIC_MOTOR_CMD, "OFF")
                st.toast("Commande envoyée: moteurs OFF")

        st.markdown("### 🤖 Servo")
        angle = st.slider("Servo Angle (0–180)", 0, 180, 90, 1)
        b3, b4 = st.columns(2)
        with b3:
            if st.button("Send Angle", use_container_width=True):
                mqtt_mgr.publish(TOPIC_SERVO_CMD, str(angle))
                st.toast(f"Servo -> {angle}°")
        with b4:
            if st.button("Quick Move (180→90)", use_container_width=True):
                mqtt_mgr.publish(TOPIC_SERVO_CMD, "180")
                time.sleep(0.25)
                mqtt_mgr.publish(TOPIC_SERVO_CMD, "90")
                st.toast("Servo mouvement envoyé")

    with right:
        st.markdown("### 🌈 RGB LED")
        mode_rgb = st.radio(
            "Mode",
            ["ON/OFF (code actuel)", "RGB JSON (si tu ajoutes le parsing JSON dans ESP32 #2)"],
            horizontal=True,
        )

        if mode_rgb.startswith("ON/OFF"):
            on = st.toggle("LED RGB ON", value=False)
            if st.button("Send LED", use_container_width=True):
                mqtt_mgr.publish(TOPIC_LEDRGB_CMD, "ON" if on else "OFF")
                st.toast(f"LED RGB -> {'ON' if on else 'OFF'}")
        else:
            r = st.slider("R", 0, 255, 255, 1)
            g = st.slider("G", 0, 255, 255, 1)
            b = st.slider("B", 0, 255, 255, 1)
            if st.button("Send Color", use_container_width=True):
                payload = json.dumps({"r": r, "g": g, "b": b})
                mqtt_mgr.publish(TOPIC_LEDRGB_CMD, payload)
                st.toast(f"LED RGB -> {payload}")

    st.markdown('<hr class="hr-neon" />', unsafe_allow_html=True)
    st.caption("Astuce: si tu utilises ThingSpeak, Node-RED doit limiter l’envoi à ≥15s par message (sinon ThingSpeak ignore/erreur).")


elif page == "ThingSpeak":
    st.subheader("📡 ThingSpeak Telemetry")
    st.markdown('<div class="small-muted">Lecture via API HTTP (cache 20s). Les champs sont supposés : field1=temp, field2=humidity, field3=flame, field4=ldr.</div>', unsafe_allow_html=True)
    st.markdown('<hr class="hr-neon" />', unsafe_allow_html=True)

    try:
        df = fetch_thingspeak_df(TS_CHANNEL_ID, TS_READ_KEY, results=180)

        if df.empty:
            st.info("Aucune donnée ThingSpeak trouvée. Vérifie channel_id/read_key et l’envoi Node-RED.")
        else:
            last = df.dropna(how="all", subset=["temp", "humidity", "flame", "ldr"]).tail(1)
            if not last.empty:
                last = last.iloc[0]
                # levels
                ts_level, ts_reason = compute_levels(last["temp"], last["flame"])
                st.markdown(
                    f"""
<div class="card">
  <div class="card-title">Last ThingSpeak Sample</div>
  <div class="kpi">
    <div><span class="kpi-value">{pd.to_datetime(last["created_at"]).strftime("%Y-%m-%d %H:%M:%S")}</span></div>
    {level_badge(ts_level)}
  </div>
  <div class="small-muted" style="margin-top:6px;">{ts_reason}</div>
</div>
""",
                    unsafe_allow_html=True,
                )

                c1, c2, c3, c4 = st.columns(4)
                temp_level = "bad" if float(last["temp"]) >= TEMP_HIGH else "warn" if float(last["temp"]) >= TEMP_MEDIUM else "ok"
                flame_level = "bad" if int(last["flame"]) < FLAME_THRESHOLD else "ok"
                with c1:
                    kpi_card("Temp", f"{last['temp']:.1f}", "°C", temp_level)
                with c2:
                    kpi_card("Humidity", f"{last['humidity']:.0f}", "%", "ok")
                with c3:
                    kpi_card("Flame", f"{int(last['flame'])}", "", flame_level)
                with c4:
                    kpi_card("LDR", f"{int(last['ldr']) if pd.notna(last['ldr']) else '—'}", "", "ok")

            st.markdown("### 📈 Trends")
            left, right = st.columns(2)
            with left:
                st.caption("Temperature")
                st.line_chart(df.set_index("created_at")[["temp"]])
            with right:
                st.caption("Humidity")
                st.line_chart(df.set_index("created_at")[["humidity"]])

            left2, right2 = st.columns(2)
            with left2:
                st.caption("Flame (ADC)")
                st.line_chart(df.set_index("created_at")[["flame"]])
            with right2:
                st.caption("LDR (ADC)")
                st.line_chart(df.set_index("created_at")[["ldr"]])

            with st.expander("Voir table (dernieres lignes)"):
                st.dataframe(df.tail(20), use_container_width=True)

    except Exception as e:
        st.error(f"Erreur lecture ThingSpeak: {e}")
        st.caption("Vérifie : channel_id + read_api_key (si privé) + accès Internet depuis Streamlit Cloud.")


elif page == "Safety":
    st.subheader("🛡️ Safety & Fail-safe")
    st.markdown('<div class="small-muted">Stop automatique des moteurs si danger (flamme/temp). Cooldown anti-spam.</div>', unsafe_allow_html=True)
    st.markdown('<hr class="hr-neon" />', unsafe_allow_html=True)

    if "last_auto_stop_ts" not in st.session_state:
        st.session_state.last_auto_stop_ts = 0.0

    danger_level, reason = compute_levels(t, fl)
    motors_on = (motors == "ON")

    left, right = st.columns([2, 1])

    with left:
        st.markdown(
            f"""
<div class="card">
  <div class="card-title">Safety State</div>
  <div class="kpi">
    <div><span class="kpi-value">{reason}</span></div>
    {level_badge(danger_level)}
  </div>
</div>
""",
            unsafe_allow_html=True,
        )

        st.write("")
        # show thresholds
        st.markdown(
            f"""
<div class="card">
  <div class="card-title">Thresholds</div>
  <div class="small-muted">
    • Flamme: danger si ADC < <b>{FLAME_THRESHOLD}</b><br/>
    • Temp: attention si ≥ <b>{TEMP_MEDIUM}°C</b>, danger si ≥ <b>{TEMP_HIGH}°C</b>
  </div>
</div>
""",
            unsafe_allow_html=True,
        )

    with right:
        auto_stop = st.toggle("Auto STOP motors", value=True)
        st.caption(f"Cooldown: {AUTO_STOP_COOLDOWN_S}s")

        if st.button("🛑 STOP now", use_container_width=True):
            mqtt_mgr.publish(TOPIC_MOTOR_CMD, "OFF")
            st.toast("STOP moteurs envoyé")

        if st.button("✅ ACK", use_container_width=True):
            st.toast("Alerte acquittée (si danger persiste, le stop auto reste possible).")

    # Auto stop logic
    if auto_stop and (danger_level == "bad") and motors_on:
        now2 = time.time()
        if now2 - st.session_state.last_auto_stop_ts > AUTO_STOP_COOLDOWN_S:
            mqtt_mgr.publish(TOPIC_MOTOR_CMD, "OFF")
            st.session_state.last_auto_stop_ts = now2
            st.toast("🛑 FAIL-SAFE: STOP moteurs envoyé (danger détecté)")

    st.markdown('<hr class="hr-neon" />', unsafe_allow_html=True)
    st.caption("Si tu veux: je peux aussi ajouter une règle 'si danger → servo ON' ou 'si danger → LED rouge' via MQTT.")


elif page == "Debug":
    st.subheader("🧪 Debug Console")
    st.markdown('<div class="small-muted">Messages bruts MQTT + JSON status + JSON capteurs.</div>', unsafe_allow_html=True)
    st.markdown('<hr class="hr-neon" />', unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        kpi_card("MQTT", "ONLINE" if snap.connected else "OFFLINE", "", "ok" if snap.connected else "bad")
    with c2:
        kpi_card("Last RX", age(snap.ts_last_any), "", "ok" if (snap.ts_last_any and (now - snap.ts_last_any) <= 12) else "warn")
    with c3:
        kpi_card("Node1 RX", age(snap.ts_last_node1), "", "ok" if snap.ts_last_node1 else "warn")

    st.markdown("### Dernier message MQTT")
    st.code(f"{snap.last_seen_topic}\n{snap.last_seen_payload}" if snap.last_seen_topic else "—", language="text")

    left, right = st.columns(2)
    with left:
        st.markdown("### esp32_2/status JSON")
        st.json(snap.last_status if snap.last_status else {})
    with right:
        st.markdown("### esp32/data JSON (Node1)")
        st.json(snap.last_node1 if snap.last_node1 else {})

    st.markdown('<hr class="hr-neon" />', unsafe_allow_html=True)
    st.caption("Si tu ne reçois rien: vérifie que le broker MQTT est accessible depuis Internet (Streamlit Cloud est externe).")
