#!/usr/bin/env python3
"""Analyze soak_events.csv -> full WiFi-characterization report + charts.

Usage:
    pip install pandas matplotlib
    python3 analyze_soak.py soak_events.csv

Outputs:
    soak_timeline.png    state + ping RTT + RSSI, shared time axis
    soak_backoff.png     backoff ladder around outages
    soak_hourly.png      outage minutes bucketed by hour-of-day
    soak_recovery.png    distribution of loss->reconnect recovery times
    soak_summary.txt     headline numbers for the PR / deploy decision
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

STATE_ORDER = ["stopped", "paused", "disconnected", "backoff",
               "connecting", "connected"]


def _f(df, col):
    return pd.to_numeric(df[col], errors="coerce") if col in df else pd.Series(dtype=float)


def main(path: str) -> None:
    df = pd.read_csv(path, parse_dates=["iso_time"])
    df["t"] = df["iso_time"]
    mgr = df[df.source == "manager"].copy()
    ping = df[df.source == "ping"].copy()
    tcp = df[df.source == "tcp"].copy()
    arp = df[df.source == "arp"].copy()
    rssi = df[df.source == "rssi"].copy()
    gaps = df[df.event == "time_gap"].copy()
    snaps = df[df.source == "snapshot"].copy()

    dur_h = (df.t.max() - df.t.min()).total_seconds() / 3600 or 1

    # ---- recovery times: connection_lost -> next connect_ok ----
    lost = mgr[mgr.event == "connection_lost"].t.tolist()
    oks = mgr[mgr.event == "connect_ok"].t.tolist()
    recoveries = []
    for L in lost:
        nxt = [o for o in oks if o > L]
        if nxt:
            recoveries.append((min(nxt) - L).total_seconds())
    rec = pd.Series(recoveries, dtype=float)

    # ---- outage intervals (manager not connected) for hourly buckets ----
    sc = mgr[mgr.event == "state_change"].copy()
    sc["new"] = sc.detail.str.split("->").str[-1]
    hourly = [0.0] * 24
    cur_state, cur_start = "disconnected", df.t.min()
    for _, r in sc.iterrows():
        if cur_state != "connected":
            span_end = r.t
            h = cur_start.hour
            hourly[h] += (span_end - cur_start).total_seconds() / 60.0
        cur_state, cur_start = r.new, r.t

    # ---- headline numbers ----
    connects_ok = int((mgr.event == "connect_ok").sum())
    connects_fail = int((mgr.event == "connect_fail").sum())
    lost_n = int((mgr.event == "connection_lost").sum())
    zombies = int((mgr.event == "zombie_socket").sum())
    hb = int((mgr.event == "heartbeat_ok").sum())
    lat = _f(mgr[mgr.event == "connect_ok"], "connect_ms").dropna()

    p_ok = ping[ping.event == "ping_ok"]
    ping_avg = _f(p_ok, "ping_avg_ms").dropna()
    ping_jit = _f(p_ok, "ping_jitter_ms").dropna()
    ping_loss = _f(ping, "ping_loss_pct").dropna()
    ping_timeouts = int((ping.event == "ping_timeout").sum())

    tcp_ok = tcp[tcp.event == "tcp_ok"]
    tcp_ms = _f(tcp_ok, "tcp_ms").dropna()
    tcp_fail = int(tcp.event.isin(["tcp_timeout", "tcp_refused", "tcp_error", "tcp_fail"]).sum())

    arp_inc = int((arp.event == "arp_incomplete").sum())
    arp_miss = int((arp.event == "arp_missing").sum())

    rssi_v = _f(rssi, "rssi_dbm").dropna()

    up = down = 0.0
    if len(snaps):
        up = float(_f(snaps, "uptime_s").iloc[-1])
        down = float(_f(snaps, "downtime_s").iloc[-1])
    uptime_pct = 100 * up / (up + down) if (up + down) else 0.0

    def q(s, p):
        return f"{s.quantile(p):.0f}" if len(s) else "n/a"

    summary = f"""SPA WIFI SOAK REPORT — {path}
================================================================
Window            : {df.t.min()}  ->  {df.t.max()}  ({dur_h:.1f}h)

CONNECTION (manager)
  uptime            : {uptime_pct:.2f}%   ({up:.0f}s up / {down:.0f}s down)
  successful conns  : {connects_ok}
  failed conns      : {connects_fail}
  mid-session losses: {lost_n}
  zombie sockets    : {zombies}
  heartbeats OK     : {hb}
  connect latency   : median {q(lat,.5)}ms  p95 {q(lat,.95)}ms  max {q(lat,1)}ms

RECOVERY (loss -> reconnect)
  events            : {len(rec)}
  median            : {q(rec,.5)}s
  p95 / max         : {q(rec,.95)}s / {q(rec,1)}s

RAW NETWORK (independent probes)
  ICMP avg RTT      : median {q(ping_avg,.5)}ms  p95 {q(ping_avg,.95)}ms
  ICMP jitter       : median {q(ping_jit,.5)}ms  p95 {q(ping_jit,.95)}ms
  ICMP loss(sample) : median {q(ping_loss,.5)}%  timeouts {ping_timeouts}
  TCP :{'4257':>4} open   : median {q(tcp_ms,.5)}ms  p95 {q(tcp_ms,.95)}ms  fails {tcp_fail}
  ARP incomplete    : {arp_inc}   ARP missing: {arp_miss}
  Wi-Fi RSSI (Mac)  : median {q(rssi_v,.5)}dBm  min {q(rssi_v,0)}dBm

TIME GAPS (Mac sleep/stall)
  gaps > 5s         : {len(gaps)}   (these are NOT spa outages)

WORST HOUR-OF-DAY (outage minutes)
  {max(range(24), key=lambda h: hourly[h]):02d}:00  ->  {max(hourly):.1f} min offline
================================================================
"""
    Path("soak_summary.txt").write_text(summary)
    print(summary)

    # ---- timeline: state + ping + rssi ----
    fig, axes = plt.subplots(3, 1, figsize=(15, 9), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1.4, 1]})
    ax1, ax2, ax3 = axes
    sc["lvl"] = sc.new.map({s: i for i, s in enumerate(STATE_ORDER)})
    ax1.step(sc.t, sc.lvl, where="post", lw=1.5, color="tab:blue")
    ax1.set_yticks(range(len(STATE_ORDER)), STATE_ORDER)
    ax1.set_title("Connection manager state")
    ax1.grid(alpha=0.3)
    for _, g in gaps.iterrows():
        ax1.axvline(g.t, color="orange", ls=":", alpha=0.7)

    if len(p_ok):
        ax2.plot(p_ok.t, _f(p_ok, "ping_avg_ms"), ".", ms=3, color="tab:green",
                 label="ICMP avg RTT")
    to = ping[ping.event == "ping_timeout"]
    ax2.plot(to.t, [0.5] * len(to), "rx", ms=6, label="ICMP timeout")
    if len(tcp_ok):
        ax2.plot(tcp_ok.t, _f(tcp_ok, "tcp_ms"), ".", ms=2, color="tab:purple",
                 alpha=0.5, label="TCP open")
    ax2.set_yscale("log")
    ax2.set_ylabel("ms")
    ax2.set_title("Raw network: ICMP RTT + TCP open time (log)")
    ax2.legend(loc="upper right")
    ax2.grid(alpha=0.3)

    if len(rssi_v):
        ax3.plot(rssi.t[:len(rssi_v)], rssi_v, "-", color="tab:red", alpha=0.7)
        ax3.set_ylabel("dBm")
    ax3.set_title("Mac Wi-Fi RSSI (context)")
    ax3.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig("soak_timeline.png", dpi=130)
    print("Wrote soak_timeline.png")

    # ---- backoff ----
    bo = mgr[mgr.event == "backoff"]
    if len(bo):
        f2, a = plt.subplots(figsize=(15, 4))
        a.plot(bo.t, _f(bo, "backoff_s"), "o-", ms=4)
        a.set_title("Backoff ladder per retry (5->10->20->...->120 during outages)")
        a.set_ylabel("seconds")
        a.grid(alpha=0.3)
        f2.tight_layout()
        f2.savefig("soak_backoff.png", dpi=130)
        print("Wrote soak_backoff.png")

    # ---- hourly outage ----
    f3, a = plt.subplots(figsize=(15, 4))
    a.bar(range(24), hourly, color="tab:orange")
    a.set_xticks(range(24))
    a.set_xlabel("hour of day")
    a.set_ylabel("minutes offline")
    a.set_title("Outage minutes by hour-of-day")
    a.grid(alpha=0.3, axis="y")
    f3.tight_layout()
    f3.savefig("soak_hourly.png", dpi=130)
    print("Wrote soak_hourly.png")

    # ---- recovery distribution ----
    if len(rec):
        f4, a = plt.subplots(figsize=(10, 4))
        a.hist(rec, bins=min(30, max(5, len(rec))), color="tab:blue",
               edgecolor="white")
        a.set_xlabel("seconds to reconnect")
        a.set_ylabel("count")
        a.set_title(f"Recovery time distribution (n={len(rec)}, "
                    f"median {rec.median():.0f}s)")
        a.grid(alpha=0.3, axis="y")
        f4.tight_layout()
        f4.savefig("soak_recovery.png", dpi=130)
        print("Wrote soak_recovery.png")
    else:
        print("No mid-session losses — no recovery chart (spa stayed solid).")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "soak_events.csv")
