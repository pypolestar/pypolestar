#!/bin/bash
set -e

ALLOY_VERSION="v1.6.1"
ARCH="amd64"
OS="linux"

ALLOY_DIR="telemetry/alloy"
mkdir -p "$ALLOY_DIR"

if [ ! -f "$ALLOY_DIR/alloy" ]; then
    echo "Downloading Grafana Alloy $ALLOY_VERSION..."
    ZIP_NAME="alloy-$OS-$ARCH.zip"
    curl -LO "https://github.com/grafana/alloy/releases/download/$ALLOY_VERSION/$ZIP_NAME"
    unzip "$ZIP_NAME" -d "$ALLOY_DIR"
    mv "$ALLOY_DIR/alloy-$OS-$ARCH" "$ALLOY_DIR/alloy"
    rm "$ZIP_NAME"
    chmod +x "$ALLOY_DIR/alloy"
fi

echo "Grafana Alloy setup complete in $ALLOY_DIR"
echo "You can now run the exporter and Alloy using ./run_telemetry.sh"
