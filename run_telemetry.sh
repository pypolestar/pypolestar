#!/bin/bash

# Configuration
# Polestar credentials
export POLESTAR_USERNAME=${POLESTAR_USERNAME:-"emondjf@gmail.com"}
export POLESTAR_VIN=${POLESTAR_VIN:-"YSREA3YB4SB021644"}

# Grafana Cloud credentials (if not already set)
if [ -z "$GRAFANA_CLOUD_PROM_URL" ]; then
    read -p "Enter Grafana Cloud Remote Write URL: " GRAFANA_CLOUD_PROM_URL
    export GRAFANA_CLOUD_PROM_URL
fi
if [ -z "$GRAFANA_CLOUD_PROM_USER" ]; then
    read -p "Enter Grafana Cloud User ID (usually a 6-digit number): " GRAFANA_CLOUD_PROM_USER
    export GRAFANA_CLOUD_PROM_USER
fi
if [ -z "$GRAFANA_CLOUD_PROM_TOKEN" ]; then
    read -s -p "Enter Grafana Cloud Token: " GRAFANA_CLOUD_PROM_TOKEN
    export GRAFANA_CLOUD_PROM_TOKEN
    echo ""
fi

# Polestar password (if not already set)
if [ -z "$POLESTAR_PASSWORD" ]; then
    read -s -p "Enter Polestar Password for $POLESTAR_USERNAME: " POLESTAR_PASSWORD
    export POLESTAR_PASSWORD
    echo ""
fi

# Ensure setup is complete
if [ ! -f "telemetry/alloy/alloy" ]; then
    ./setup_alloy.sh
fi

# Start Polestar Exporter
uv run python polestar_exporter.py &
EXPORTER_PID=$!
echo "Polestar Exporter started with PID $EXPORTER_PID"

# Wait for exporter to be ready
sleep 2

# Start Grafana Alloy
./telemetry/alloy/alloy run --storage.path=telemetry/alloy/data config.alloy &
ALLOY_PID=$!
echo "Grafana Alloy started with PID $ALLOY_PID"

# Cleanup on exit
trap "kill $EXPORTER_PID $ALLOY_PID; exit" INT TERM
wait
