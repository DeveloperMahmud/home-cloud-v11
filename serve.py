from waitress import serve

import app as home_cloud


def get_lan_ip():
    """Return a local LAN IPv4 address without contacting the internet."""
    return home_cloud.detect_lan_ip() or None


if __name__ == "__main__":
    home_cloud.init_db()
    port = int(home_cloud.config_value("port") or 8080)
    app_name = str(home_cloud.config_value("app_name") or "Home Cloud")
    home_cloud.server_event(f"{app_name} {home_cloud.APP_VERSION} starting with Waitress on port {port}.")
    print("Asset root:", home_cloud.ROOT)
    print(f"Open http://127.0.0.1:{port}")
    lan_ip = get_lan_ip()
    print(f"LAN access: http://{lan_ip}:{port}" if lan_ip else "LAN access: IP address unavailable")
    print("Internet required: No")
    serve(home_cloud.app, host="0.0.0.0", port=port, threads=8, channel_timeout=900)
