#!/usr/bin/env python3
"""
Dongle Trading — Investor Dashboard API
========================================
Serves real-time engine state, trade logs, equity curves, and market data
for the investor-facing monitoring dashboard.
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
BACKTEST_DIR = PROJECT_ROOT / "backtest_results"

PROFILES = ["conservative", "aggressive", "ultra"]
PROFILE_META = {
    "conservative": {"risk": "2%", "lev": "3x", "label": "Conservative", "color": "#4f98a3"},
    "aggressive":   {"risk": "3%", "lev": "3x", "label": "Aggressive",   "color": "#da7101"},
    "ultra":        {"risk": "5%", "lev": "3x", "label": "Ultra",        "color": "#a13544"},
}


def read_state(profile: str) -> dict:
    """Read engine state file for a given profile."""
    state_file = DATA_DIR / f"v13_state_{profile}.json"
    if state_file.exists():
        try:
            with open(state_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"version": "v13.0", "realized_pnl": 0, "positions": {}, "trade_log": [], "start_equity": 4804.67}


def parse_log_entries(profile: str, limit: int = 200) -> list:
    """Parse last N log lines for a profile."""
    log_file = LOG_DIR / f"v13_{profile}.log"
    if not log_file.exists():
        return []
    try:
        with open(log_file) as f:
            lines = f.readlines()
        entries = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            # Parse: 2026-03-20 14:21:27 [INFO] message
            m = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] (.*)', line)
            if m:
                entries.append({
                    "ts": m.group(1),
                    "level": m.group(2),
                    "msg": m.group(3)
                })
        return entries
    except IOError:
        return []


def get_dashboard_data() -> dict:
    """Compile all data needed for the dashboard."""
    profiles_data = {}
    
    for p in PROFILES:
        state = read_state(p)
        meta = PROFILE_META[p]
        
        # Calculate current equity
        start_eq = state.get("start_equity", 4804.67)
        realized = state.get("realized_pnl", 0)
        current_eq = start_eq + realized
        
        # Open positions
        positions = state.get("positions", {})
        open_pos = []
        unrealized_pnl = 0
        for sym, pos in positions.items():
            if isinstance(pos, dict):
                open_pos.append(pos)
                # Estimate unrealized P&L if available
                upnl = pos.get("partial_pnl", 0)
                unrealized_pnl += upnl
        
        # Trade log
        trade_log = state.get("trade_log", [])
        
        # Win rate from trades
        if trade_log:
            wins = sum(1 for t in trade_log if t.get("pnl", 0) > 0)
            wr = round(wins / len(trade_log) * 100, 1) if trade_log else 0
        else:
            wr = 0
        
        # Equity history from trade log
        eq_history = [{"ts": state.get("saved_at", ""), "equity": start_eq}]
        running_eq = start_eq
        for t in trade_log:
            running_eq += t.get("pnl", 0)
            eq_history.append({
                "ts": t.get("exit_time", t.get("xt", "")),
                "equity": round(running_eq, 2)
            })
        
        profiles_data[p] = {
            "meta": meta,
            "state": {
                "version": state.get("version", "v13.0"),
                "last_update": state.get("saved_at", "N/A"),
                "global_bar": state.get("global_bar", 0),
                "start_equity": round(start_eq, 2),
                "current_equity": round(current_eq, 2),
                "realized_pnl": round(realized, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "total_pnl": round(realized + unrealized_pnl, 2),
                "return_pct": round((realized + unrealized_pnl) / start_eq * 100, 2) if start_eq > 0 else 0,
                "open_positions": len(open_pos),
                "total_trades": len(trade_log),
                "win_rate": wr,
            },
            "positions": open_pos,
            "trades": trade_log[-50:],  # Last 50 trades
            "equity_history": eq_history,
        }
    
    # Load backtest reference data
    backtest_ref = {}
    bt_file = BACKTEST_DIR / "v13_3profiles.json"
    if bt_file.exists():
        try:
            with open(bt_file) as f:
                bt_data = json.load(f)
            for item in bt_data:
                label = item.get("label", "")
                if "Conservative" in label:
                    backtest_ref["conservative"] = item
                elif "Aggressive" in label:
                    backtest_ref["aggressive"] = item
                elif "Ultra" in label:
                    backtest_ref["ultra"] = item
        except (json.JSONDecodeError, IOError):
            pass
    
    # Load backtest equity curve
    eq_file = BACKTEST_DIR / "v13_equity_curve.json"
    backtest_equity = []
    if eq_file.exists():
        try:
            with open(eq_file) as f:
                backtest_equity = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    
    # Load backtest trades
    trades_file = BACKTEST_DIR / "v13_trades.json"
    backtest_trades = []
    if trades_file.exists():
        try:
            with open(trades_file) as f:
                backtest_trades = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine_version": "v13.0",
        "testnet_balance": 4804.67,
        "profiles": profiles_data,
        "backtest_ref": backtest_ref,
        "backtest_equity": backtest_equity,
        "backtest_trades": backtest_trades[-100:],  # Last 100 for display
    }


def get_log_data(profile: str = "all") -> dict:
    """Get parsed log entries."""
    if profile == "all":
        logs = {}
        for p in PROFILES:
            logs[p] = parse_log_entries(p)
        return logs
    return {profile: parse_log_entries(profile)}


class DashboardHandler(SimpleHTTPRequestHandler):
    """HTTP handler serving both static files and API endpoints."""
    
    def __init__(self, *args, **kwargs):
        # Serve static files from the dashboard directory
        super().__init__(*args, directory=str(Path(__file__).parent), **kwargs)
    
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == "/api/dashboard":
            self._json_response(get_dashboard_data())
        elif path == "/api/logs":
            params = parse_qs(parsed.query)
            profile = params.get("profile", ["all"])[0]
            self._json_response(get_log_data(profile))
        elif path == "/api/health":
            self._json_response({"status": "ok", "time": datetime.now(timezone.utc).isoformat()})
        else:
            # Serve static files
            super().do_GET()
    
    def _json_response(self, data: dict):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def log_message(self, format, *args):
        # Suppress request logs
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"Dongle Trading Dashboard API running on port {port}")
    server.serve_forever()
