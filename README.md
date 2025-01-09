# Python for Polestar

This library is not affiliated with nor supported by [Polestar](https://www.polestar.com).


## Data Models

Data models for returned information are described in [`pypolestar/models.py`](pypolestar/models.py).


## Example

```python
from pypolestar import PolestarApi

api = PolestarApi(username=USERNAME, password=PASSWORD, vins=[VIN])

# initialize API
await api.async_init()

# fetch latest data (do not call to frequently)
await api.update_latest_data(vin=VIN)

# get specific data
car_information = api.get_car_information(vin=VIN)
car_battery = api.get_car_battery(vin=VIN)
car_odometer = api.get_car_odometer(vin=VIN)
```
