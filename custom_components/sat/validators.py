from urllib.parse import urlparse


def valid_serial_device(value: str):
    if value.startswith("socket://"):
        parsed_url = urlparse(value)
        if parsed_url.hostname and parsed_url.port:
            return True
    elif value.startswith("/dev/"):
        return True

    return False
