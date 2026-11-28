# Python for Polestar

This library is not affiliated with nor supported by [Polestar](https://www.polestar.com).


## Data Models

Data models for returned information are described in [`pypolestar/models.py`](pypolestar/models.py)
(GraphQL) and [`pypolestar/grpc_models.py`](pypolestar/grpc_models.py) (gRPC).


## Example

```python
from pypolestar import PolestarApi

api = PolestarApi(username=USERNAME, password=PASSWORD, vins=[VIN])

# initialize API
await api.async_init()

# fetch latest telematics (contains both battery and odometer) for VIN
await api.update_latest_data(vin=VIN, update_telematics=True)

# get specific data for VIN
car_information = api.get_car_information(vin=VIN)
car_telematics = api.get_car_telematics(vin=VIN)

# gRPC: battery/target SOC plus exterior, health, odometer, climate, availability,
# pre-cleaning, location, mycars, amp limit and charge schedule
grpc_battery = api.get_grpc_battery(vin=VIN)
grpc_exterior = api.get_grpc_exterior(vin=VIN)  # doors/windows/locks, best-effort
grpc_mycars = api.get_grpc_mycars(vin=VIN)  # installed software version, live-verified
```


## Scope of the added gRPC services

The ten services added beyond battery/target SOC are **read-only telemetry only** -- no
remote/write commands (lock, climate start, charge target, etc.) are implemented, and their
message field layout was determined two ways:

- Seven (exterior, health, odometer, climate, availability, pre-cleaning, location) had their
  field layout cross-referenced from multiple independent public reverse-engineering projects
  (see each `.proto` file's header comment for the specific service), then spot-checked live.
- Three (mycars, amp limit, charge schedule) were reverse-engineered directly against a real
  account: the known endpoint path was called with a generic request and the raw protobuf wire
  format was decoded by hand, since no external schema existed for these anywhere.

All ten have been validated against one real Polestar 2 in a parked, idle state: odometer
cross-verified byte-for-byte against the existing GraphQL field, GPS correctly placed the car
in Gothenburg, `get_mycars` matched known VIN/model/plate exactly and surfaced the installed
software version (not available via GraphQL at all), and `get_charge_schedule` returned a real
23:00-06:00 overnight charging window. Still unverified: active-charging/active-climate states,
other Polestar models, and populated charge locations (this account has none saved).

Each new `is_<x>_supported(vin)` returning `False` after a
`PERMISSION_DENIED`/`UNIMPLEMENTED`/`NOT_FOUND` response can mean either that the vehicle
genuinely doesn't serve that data, or that the field layout guessed for it is wrong -- these
two cases can't be told apart without a live vehicle to test against.
