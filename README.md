# Python for Polestar

This library is not an official app affiliated with Polestar Automotive.


## Example

```python
from pypolestar import PolestarApi

api = PolestarApi(username=USERNAME, password=PASSWORD, vins=[VIN])

await api.async_init()
await api.get_latest_data(vin=VIN)

car_information = api.get_car_information(vin=VIN)
car_battery = api.get_car_battery(vin=VIN)
car_odometer = api.get_car_odometer(vin=VIN)
```
