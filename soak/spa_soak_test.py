#!/usr/bin/env python3
"""24-hour WiFi-characterization soak for SpaConnectionManager (macOS).

Runs the EXACT connection.py that ships in the HA integration against the
real spa, and captures everything useful for diagnosing a flaky WiFi module:

  MANAGER (push, event-driven)
    connect attempts/ok/fail + connect_ms, zombie sockets, heartbeats,
    connection losses (with reason), backoff ladder, pause/resume.

  NETWORK PROBES (every --probe-interval, default 20s)
    * ICMP: 3-packet burst -> min/avg/max RTT + jitter + %loss
    * TCP : time to open :4257 (independent of pybalboa) -> tcp_ms / refused
    * ARP : MAC present / incomplete / missing
    * RSSI: Wi-Fi signal strength of THIS Mac (context for correlation)

  WATCHDOG (every 1s)
    Detects wall-clock jumps > 5s => Mac sleep / process stall, logged as
    a `time_gap` row so outages aren't mis-attributed to the spa.

  SNAPSHOT (every 5m)
    Cumulative uptime/downtime + counters.

Usage:
    source venv/bin/activate            # venv with pybalboa installed
    python3 spa_soak_test.py --host 192.168.1.100 --hours 24

Outputs (cwd): soak_events.csv, soak.log
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import re
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from connection import ManagerConfig, SpaConnectionManager  # noqa: E402

CSV_FIELDS = [
    "iso_time", "ts", "source", "event", "state", "detail",
    "attempt", "backoff_s", "connect_ms",
    "ping_min_ms", "ping_avg_ms", "ping_max_ms", "ping_jitter_ms",
    "ping_loss_pct", "tcp_ms", "mac", "rssi_dbm", "gap_s",
    "uptime_s", "downtime_s",
    "connects_ok", "connects_failed", "connections_lost",
]


class SoakLogger:
    """CSV writer with flush-per-row so nothing is lost on crash/Ctrl-C."""

    def __init__(self, path: Path) -> None:
        self._fh = path.open("w", newline="")
        self._writer = csv.DictWriter(
            self._fh, fieldnames=CSV_FIELDS, extrasaction="ignore"
        )
        self._writer.writeheader()

    def write(self, source: str, **fields) -> None:
        ts = fields.pop("ts", time.time())
        row = {
            "iso_time": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
            "ts": f"{ts:.3f}", "source": source, **fields,
        }
        self._writer.writerow(row)
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


_PING_RE = re.compile(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms")
_LOSS_RE = re.compile(r"([\d.]+)% packet loss")


async def ping_probe(host, log, interval, stop):
    """3-packet ICMP burst -> min/avg/max/jitter + loss (macOS ping)."""
    while not stop.is_set():
        fields = {"event": "ping_timeout", "ping_loss_pct": 100.0}
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping", "-c", "3", "-t", "3", host,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
            text = out.decode()
            loss = _LOSS_RE.search(text)
            rtt = _PING_RE.search(text)
            if rtt:
                mn, avg, mx, jit = (float(x) for x in rtt.groups())
                fields = {
                    "event": "ping_ok",
                    "ping_min_ms": mn, "ping_avg_ms": avg,
                    "ping_max_ms": mx, "ping_jitter_ms": jit,
                    "ping_loss_pct": float(loss.group(1)) if loss else 0.0,
                }
            elif loss:
                fields["ping_loss_pct"] = float(loss.group(1))
        except Exception:
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
        log.write("ping", **fields)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def tcp_probe(host, port, log, interval, stop):
    """Independent TCP open timing to the Balboa port (bypasses pybalboa)."""
    while not stop.is_set():
        t0 = time.monotonic()
        event, tcp_ms = "tcp_fail", None
        try:
            fut = asyncio.open_connection(host, port)
            reader, writer = await asyncio.wait_for(fut, timeout=5)
            tcp_ms = (time.monotonic() - t0) * 1000
            event = "tcp_ok"
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=2)
            except Exception:
                pass
        except asyncio.TimeoutError:
            event = "tcp_timeout"
        except ConnectionRefusedError:
            event = "tcp_refused"
        except Exception:
            event = "tcp_error"
        log.write("tcp", event=event,
                  tcp_ms=round(tcp_ms, 1) if tcp_ms else None)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def arp_probe(host, log, interval, stop):
    while not stop.is_set():
        mac, event = None, "arp_missing"
        try:
            proc = await asyncio.create_subprocess_exec(
                "arp", "-n", host,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
            text = out.decode()
            if " at " in text and "incomplete" not in text:
                mac = text.split(" at ")[1].split(" ")[0]
                event = "arp_ok"
            elif "incomplete" in text:
                event = "arp_incomplete"
        except Exception:
            pass
        log.write("arp", event=event, mac=mac)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


_RSSI_RE = re.compile(r"(?:agrCtlRSSI|RSSI)\s*[:=]\s*(-?\d+)")
_AIRPORT = (
    "/System/Library/PrivateFrameworks/Apple80211.framework/"
    "Versions/Current/Resources/airport"
)


async def rssi_probe(log, interval, stop):
    """Sample this Mac's Wi-Fi RSSI for correlation with spa drops."""
    while not stop.is_set():
        rssi = None
        for cmd in (["/usr/bin/wdutil", "info"], [_AIRPORT, "-I"]):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                m = _RSSI_RE.search(out.decode())
                if m:
                    rssi = int(m.group(1))
                    break
            except Exception:
                continue
        log.write("rssi", event="rssi",
                  rssi_dbm=rssi if rssi is not None else "")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def watchdog(log, stop):
    """Detect wall-clock gaps (Mac sleep/stall) so we don't blame the spa."""
    last = time.monotonic()
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
        now = time.monotonic()
        gap = now - last
        if gap > 5.0:
            log.write("watchdog", event="time_gap", gap_s=round(gap, 1))
        last = now


async def snapshot(mgr, log, interval, stop):
    while not stop.is_set():
        s = mgr.stats
        log.write(
            "snapshot", event="stats", state=mgr.state.value,
            uptime_s=round(s.total_uptime_s + s.current_uptime_s, 1),
            downtime_s=round(s.total_downtime_s + s.current_downtime_s, 1),
            connects_ok=s.connects_ok, connects_failed=s.connects_failed,
            connections_lost=s.connections_lost,
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Spa WiFi module IP")
    parser.add_argument("--port", type=int, default=4257)
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--probe-interval", type=float, default=20.0)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler("soak.log"), logging.StreamHandler()],
    )
    logging.getLogger("connection").setLevel(logging.DEBUG)

    log = SoakLogger(Path("soak_events.csv"))
    stop = asyncio.Event()

    def _sig(*_):
        logging.info("Interrupted — clean shutdown")
        stop.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    manager = SpaConnectionManager(
        host=args.host, port=args.port, config=ManagerConfig(),
        event_cb=lambda ev: log.write("manager", **ev),
    )

    logging.info(
        "WiFi soak: %s:%s for %.1fh, probes every %.0fs — CSV: soak_events.csv",
        args.host, args.port, args.hours, args.probe_interval,
    )
    await manager.start()

    iv = args.probe_interval
    tasks = [
        asyncio.create_task(ping_probe(args.host, log, iv, stop)),
        asyncio.create_task(tcp_probe(args.host, args.port, log, iv, stop)),
        asyncio.create_task(arp_probe(args.host, log, iv, stop)),
        asyncio.create_task(rssi_probe(log, iv, stop)),
        asyncio.create_task(watchdog(log, stop)),
        asyncio.create_task(snapshot(manager, log, 300, stop)),
    ]

    try:
        await asyncio.wait_for(stop.wait(), timeout=args.hours * 3600)
    except asyncio.TimeoutError:
        logging.info("Soak duration reached")
    finally:
        stop.set()
        await manager.stop()
        await asyncio.gather(*tasks, return_exceptions=True)
        s = manager.stats
        total = s.total_uptime_s + s.total_downtime_s or 1
        logging.info(
            "DONE. ok=%d fail=%d lost=%d | uptime=%.2f%% (%.0fs up/%.0fs down)",
            s.connects_ok, s.connects_failed, s.connections_lost,
            100 * s.total_uptime_s / total, s.total_uptime_s, s.total_downtime_s,
        )
        log.close()


if __name__ == "__main__":
    asyncio.run(main())
