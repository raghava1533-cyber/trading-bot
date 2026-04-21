# dashboard/app.py
import streamlit as st
import redis
import json

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

st.title("📊 Live Options Dashboard")

data = r.get("live")

if data:
    data = json.loads(data)

    st.metric("Spot", data["spot"])
    st.metric("Delta", data["delta"])

    st.write("Selected Strikes")
    st.write(data["ce"], data["pe"])
else:
    st.write("Waiting for data...")