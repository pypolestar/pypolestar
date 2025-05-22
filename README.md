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

# fetch latest telematics (contains both battery and odometer) for VIN
await api.update_latest_data(vin=VIN, update_telematics=True)

# get specific data for VIN
car_information = api.get_car_information(vin=VIN)
car_telematics = api.get_car_telematics(vin=VIN)
```
