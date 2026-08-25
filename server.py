import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen


PORT = int(os.environ.get("PORT", "3000"))
GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


class KrishiHandler(SimpleHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.end_headers()

    def do_POST(self):
        if self.path != "/api/delivery-distance":
            self._json(404, {"error": "Not found"})
            return

        api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
        if not api_key:
            self._json(503, {"error": "Delivery distance service is not configured"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            request_data = json.loads(self.rfile.read(length))
            origin = str(request_data.get("origin", "")).strip()
            destination = str(request_data.get("destination", "")).strip()
            if not origin or not destination:
                raise ValueError("Origin and destination are required")

            routes_request = json.dumps({
                "origin": {"address": origin},
                "destination": {"address": destination},
                "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_AWARE",
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
                self._json(422, {"error": "Google Maps could not find a drivable route"})
                return
            self._json(200, {
                "distanceKm": round(float(routes[0]["distanceMeters"]) / 1000, 2),
                "duration": routes[0].get("duration"),
                "source": "google-routes",
            })
        except ValueError as error:
            self._json(400, {"error": str(error)})
        except Exception:
            self._json(502, {"error": "Google Maps distance lookup failed"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), KrishiHandler).serve_forever()