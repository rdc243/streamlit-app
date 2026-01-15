import requests
import pandas as pd
from streamlit_autorefresh import st_autorefresh

TS_CHANNEL_ID = str(st.secrets["thingspeak"]["channel_id"])
TS_READ_KEY = st.secrets["thingspeak"].get("read_api_key", "")

@st.cache_data(ttl=20)  # cache 20s pour ne pas spammer ThingSpeak
def fetch_thingspeak_df(channel_id: str, read_key: str, results: int = 60) -> pd.DataFrame:
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
        rows.append({
            "created_at": f.get("created_at"),
            "temp": f.get("field1"),
            "humidity": f.get("field2"),
            "flame": f.get("field3"),
            "ldr": f.get("field4"),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    for c in ["temp", "humidity", "flame", "ldr"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["created_at"]).sort_values("created_at")
    return df
