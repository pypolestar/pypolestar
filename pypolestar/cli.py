import argparse
import asyncio
import json
import logging
import os
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
    parser.add_argument("--verbose", action="store_true", help="Fetch verbose data")
    parser.add_argument("--dump", action="store_true", help="Dump vehicle data to VIN.json")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if not (username := args.username or os.getenv("POLESTAR_USERNAME") or input("Polestar ID: ")):
        logging.error("Empty username provided")
        raise SystemExit(1)

    if not (password := os.getenv("POLESTAR_PASSWORD") or getpass("Password: ")):
        logging.error("Empty password provided")
        raise SystemExit(1)

    api = PolestarApi(username=username, password=password)

    try:
        await api.async_init(verbose=args.verbose)
    except PolestarAuthException as exc:
        logging.error("Authentication failed for %s", username)
        raise SystemExit(1) from exc

    logging.info("Found VINs: %s", api.get_available_vins())

    if args.dump:
        for vin in [args.vin] if args.vin else api.get_available_vins():
            await api.update_latest_data(vin)
            dump_api_data(api, vin)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
