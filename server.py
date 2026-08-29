import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


PORT = int(os.environ.get("PORT", "3000"))
GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
KRISHI_GOLD_ORIGIN = "Pipra, Madhepur, Madhubani, Bihar 847408, India"


def geocode(address, api_key):
    query = urlencode({
        "address": address if "india" in address.lower() else address + ", India",
        "key": api_key,
        "region": "in",
    })
    request = Request(GOOGLE_GEOCODE_URL + "?" + query, method="GET")
    try:
        with urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        # Routes API can geocode address strings itself, so a separately
        # enabled Geocoding API is helpful but not required for delivery.
        return None, "UNAVAILABLE"
    if data.get("status") != "OK" or not data.get("results"):
        return None, data.get("status", "UNKNOWN")
    location = data["results"][0]["geometry"]["location"]
    return {"latitude": location["lat"], "longitude": location["lng"]}, "OK"


class KrishiHandler(SimpleHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._json(405, {"success": False, "error": "Use POST for the delivery distance request."})
            return
        super().do_GET()

    def do_POST(self):
        if self.path != "/api/delivery-distance":
            self._json(404, {"success": False, "error": "API route not found."})
            return

        api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
        if not api_key:
            self._json(503, {"success": False, "error": "Delivery distance service is not configured"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            request_data = json.loads(self.rfile.read(length))
            if not isinstance(request_data, dict):
                raise ValueError("Request body must be a JSON object")
            destination = str(request_data.get("destination", "")).strip()
            if not destination:
                raise ValueError("A complete delivery destination is required")

            origin_location, _ = geocode(KRISHI_GOLD_ORIGIN, api_key)
            destination_location, destination_status = geocode(destination, api_key)
            if not destination_location and destination_status == "ZERO_RESULTS":
                self._json(422, {
                    "success": False,
                    "error": "Address could not be located. Please correct the delivery address and landmark.",
                    "geocodeStatus": destination_status,
                })
                return

            routes_request = json.dumps({
                "origin": (
                    {"location": {"latLng": origin_location}}
                    if origin_location
                    else {"address": KRISHI_GOLD_ORIGIN}
                ),
                "destination": (
                    {"location": {"latLng": destination_location}}
                    if destination_location
                    else {"address": destination}
                ),
                "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_UNAWARE",
                "units": "METRIC",
            }).encode("utf-8")
            request = Request(
                GOOGLE_ROUTES_URL,
                data=routes_request,
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": api_key,
                    "X-Goog-FieldMask": "routes.distanceMeters,routes.duration",
                },
                method="POST",
            )
            with urlopen(request, timeout=15) as response:
                routes_data = json.loads(response.read().decode("utf-8"))
            routes = routes_data.get("routes") or []
            if not routes or not isinstance(routes[0].get("distanceMeters"), (int, float)):
                self._json(422, {
                    "success": False,
                    "error": "Address could not be located or routed. Please correct the delivery address and landmark.",
                })
                return
            distance_km = round(float(routes[0]["distanceMeters"]) / 1000, 2)
            delivery_charge = round(max(0, distance_km - 2) * 25, 2)
            self._json(200, {
                "success": True,
                "distanceKm": distance_km,
                "deliveryCharge": delivery_charge,
            })
        except json.JSONDecodeError:
            self._json(400, {"success": False, "error": "Request body must be valid JSON"})
        except ValueError as error:
            self._json(400, {"success": False, "error": str(error)})
        except (HTTPError, URLError, TimeoutError, KeyError, TypeError, AttributeError):
            self._json(502, {"success": False, "error": "Google Maps distance lookup failed"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), KrishiHandler).serve_forever()