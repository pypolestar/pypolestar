import asyncio
import logging
import os
import time
from prometheus_client import start_http_server, Gauge
from pypolestar import PolestarApi

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("polestar_exporter")

# Prometheus Metrics
BATTERY_LEVEL = Gauge('polestar_battery_level_percentage', 'Battery charge level percentage', ['vin', 'model'])
RANGE_KM = Gauge('polestar_range_km', 'Estimated range in kilometers', ['vin', 'model'])
RANGE_MI = Gauge('polestar_range_miles', 'Estimated range in miles', ['vin', 'model'])
CHARGING_POWER = Gauge('polestar_charging_power_watts', 'Current charging power in watts', ['vin', 'model'])
CHARGING_CURRENT = Gauge('polestar_charging_current_amps', 'Current charging current in amps', ['vin', 'model'])
CHARGING_VOLTAGE = Gauge('polestar_charging_voltage_volts', 'Current charging voltage in volts', ['vin', 'model'])
AVG_CONSUMPTION = Gauge('polestar_avg_consumption_kwh_100km', 'Average energy consumption kWh/100km', ['vin', 'model', 'type'])
TOTAL_ENERGY = Gauge('polestar_total_energy_kwh', 'Total energy consumption in kWh', ['vin', 'model', 'type'])
ODOMETER = Gauge('polestar_odometer_km', 'Odometer reading in km', ['vin', 'model'])

class PolestarExporter:
    def __init__(self, username, password, vins):
        self.api = PolestarApi(username=username, password=password, vins=vins)
        self.vins = vins
        self._init_metrics()

    def _init_metrics(self):
        # Initialize metrics with 0 to ensure they exist even before first update
        for vin in self.vins:
            labels = {'vin': vin, 'model': 'Unknown'}
            BATTERY_LEVEL.labels(**labels).set(0)
            RANGE_KM.labels(**labels).set(0)
            RANGE_MI.labels(**labels).set(0)
            CHARGING_POWER.labels(**labels).set(0)
            CHARGING_CURRENT.labels(**labels).set(0)
            CHARGING_VOLTAGE.labels(**labels).set(0)
            ODOMETER.labels(**labels).set(0)
            # Consumption metrics with types
            for t in ['lifetime', 'automatic', 'since_charge']:
                AVG_CONSUMPTION.labels(vin=vin, model='Unknown', type=t).set(0)
            for t in ['lifetime', 'since_charge']:
                TOTAL_ENERGY.labels(vin=vin, model='Unknown', type=t).set(0)

    async def update_metrics(self):
        try:
            await self.api.async_init()
            for vin in self.vins:
                logger.info(f"Updating metrics for VIN: {vin}")
                await self.api.update_latest_data(vin=vin)
                
                car_info = self.api.get_car_information(vin)
                car_telematics = self.api.get_car_telematics(vin)
                grpc_battery = self.api.get_grpc_battery(vin)
                
                model = getattr(car_info, 'model_name', 'Unknown')
                labels = {'vin': vin, 'model': model}

                # Odometer from telematics
                if car_telematics and car_telematics.odometer:
                    ODOMETER.labels(**labels).set(car_telematics.odometer.odometer_meters / 1000.0)

                # Metrics from gRPC (preferred as it's richer)
                if grpc_battery:
                    BATTERY_LEVEL.labels(**labels).set(grpc_battery.battery_charge_level_percentage or 0)
                    RANGE_KM.labels(**labels).set(grpc_battery.estimated_distance_to_empty_km or 0)
                    RANGE_MI.labels(**labels).set(grpc_battery.estimated_distance_to_empty_miles or 0)
                    CHARGING_POWER.labels(**labels).set(grpc_battery.charging_power_watts or 0)
                    CHARGING_CURRENT.labels(**labels).set(grpc_battery.charging_current_amps or 0)
                    CHARGING_VOLTAGE.labels(**labels).set(grpc_battery.charging_voltage_volts or 0)
                    
                    if grpc_battery.average_energy_consumption_kwh_per_100km:
                        AVG_CONSUMPTION.labels(vin=vin, model=model, type='lifetime').set(grpc_battery.average_energy_consumption_kwh_per_100km)
                    if grpc_battery.average_energy_consumption_kwh_per_100km_automatic:
                        AVG_CONSUMPTION.labels(vin=vin, model=model, type='automatic').set(grpc_battery.average_energy_consumption_kwh_per_100km_automatic)
                    if grpc_battery.average_energy_consumption_kwh_per_100km_since_charge:
                        AVG_CONSUMPTION.labels(vin=vin, model=model, type='since_charge').set(grpc_battery.average_energy_consumption_kwh_per_100km_since_charge)
                    
                    if grpc_battery.total_energy_consumption_wh:
                        TOTAL_ENERGY.labels(vin=vin, model=model, type='lifetime').set(grpc_battery.total_energy_consumption_wh / 1000.0)
                    if grpc_battery.total_energy_consumption_wh_since_charge:
                        TOTAL_ENERGY.labels(vin=vin, model=model, type='since_charge').set(grpc_battery.total_energy_consumption_wh_since_charge / 1000.0)
                
                logger.info(f"Metrics updated for {model} ({vin})")
                
        except Exception as e:
            logger.error(f"Error updating metrics: {e}")

async def main():
    username = os.getenv("POLESTAR_USERNAME")
    password = os.getenv("POLESTAR_PASSWORD")
    
    # Check for systemd credentials if password is not in environment
    creds_dir = os.getenv("CREDENTIALS_DIRECTORY")
    if not password and creds_dir:
        password_file = os.path.join(creds_dir, "polestar_password")
        if os.path.exists(password_file):
            with open(password_file) as f:
                password = f.read().strip()
                logger.info("Loaded Polestar password from systemd credentials")

    vin = os.getenv("POLESTAR_VIN")
    port = int(os.getenv("EXPORTER_PORT", 9876))
    interval = int(os.getenv("UPDATE_INTERVAL", 300)) # Default 5 mins

    if not username or not password or not vin:
        logger.error("Missing POLESTAR_USERNAME, POLESTAR_PASSWORD, or POLESTAR_VIN")
        return

    exporter = PolestarExporter(username, password, [vin])
    
    # Start Prometheus HTTP server
    start_http_server(port)
    logger.info(f"Polestar Prometheus Exporter started on port {port}")

    while True:
        await exporter.update_metrics()
        logger.info(f"Sleeping for {interval} seconds...")
        await asyncio.sleep(interval)

if __name__ == "__main__":
    asyncio.run(main())
