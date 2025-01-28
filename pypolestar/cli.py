import argparse
import asyncio
import json
import logging
from getpass import getpass

from . import PolestarApi
from .exceptions import PolestarAuthException


def dump_api_data(api: PolestarApi, vin: str) -> None:
    filename = f"{vin}.json"
    with open(filename, "w") as fp:
        json.dump(api.data_by_vin[vin], fp, indent=4)
    logging.info("Wrote vehicle data to %s", filename)


async def async_main():
    """Main function"""

    parser = argparse.ArgumentParser(description="Polestar API command line interface")

    parser.add_argument("--username", type=str, help="Polestar ID")
    parser.add_argument("--vin", type=str, help="VIN")
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Dump vehicle data to VIN.json",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output",
    )

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    username = args.username or input("Polestar ID: ")
    password = getpass("Password: ")

    if not password:
        logging.error("Empty password provided")
        raise SystemExit(1)

    api = PolestarApi(username=username, password=password)

    try:
        await api.async_init()
    except PolestarAuthException as exc:
        logging.error("Authentication failed for %s", username)
        raise SystemExit(1) from exc

    logging.info("Found VINs: %s", api.get_available_vins())

    if args.dump:
        for vin in [args.vin] if args.vin else api.get_available_vins():
            await api.update_latest_data(vin, update_telematics=True, update_battery=True, update_odometer=True)
            dump_api_data(api, vin)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
