import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


PORT = int(os.environ.get("PORT", "3000"))
GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
INDIA_POST_PINCODE_URL = "https://api.postalpincode.in/pincode"
OPENSTREETMAP_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
KRISHI_GOLD_ORIGIN = "Pipra, Madhepur, Madhubani, Bihar 847408, India"


def geocode(address, api_key):
    query = urlencode({"address": address, "key": api_key})
    request = Request(GOOGLE_GEOCODE_URL + "?" + query, method="GET")
    with urlopen(request, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("status") != "OK" or not data.get("results"):
        return None, data.get("status", "UNKNOWN")
    location = data["results"][0]["geometry"]["location"]
    return {"latitude": location["lat"], "longitude": location["lng"]}, "OK"


def _component(components, *types):
    for component in components:
        if any(component.get("types") and kind in component["types"] for kind in types):
            return component.get("long_name", "")
    return ""


def address_suggestions(query, api_key):
    params = urlencode({"address": query + ", India", "key": api_key, "region": "in"})
    request = Request(GOOGLE_GEOCODE_URL + "?" + params, method="GET")
    with urlopen(request, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        return fallback_address_suggestions(query), "FALLBACK"

    suggestions = []
    seen = set()
    for result in data.get("results", [])[:5]:
        components = result.get("address_components", [])
        state = _component(components, "administrative_area_level_1")
        district = _component(components, "administrative_area_level_2")
        city = _component(
            components,
            "locality",
            "postal_town",
            "administrative_area_level_3",
            "sublocality",
            "sublocality_level_1",
        )
        pincode = _component(components, "postal_code")
        formatted = result.get("formatted_address", "").replace(", India", "")
        key = formatted.lower()
        if formatted and key not in seen:
            seen.add(key)
            suggestions.append({
                "label": formatted,
                "address": formatted,
                "city": city,
                "district": district,
                "state": state,
                "pincode": pincode,
            })
    return suggestions, "OK"


def fallback_address_suggestions(query):
    params = urlencode({
        "q": query + ", India",
        "format": "jsonv2",
        "addressdetails": "1",
        "countrycodes": "in",
        "limit": "5",
    })
    request = Request(
        OPENSTREETMAP_SEARCH_URL + "?" + params,
        headers={"Accept": "application/json", "User-Agent": "KrishiGoldWebsite/1.0"},
        method="GET",
    )
    with urlopen(request, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))
    suggestions = []
    seen = set()
    for result in data if isinstance(data, list) else []:
        address = result.get("address", {})
        state = address.get("state", "")
        district = address.get("state_district") or address.get("county", "")
        city = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("municipality")
            or address.get("suburb")
            or ""
        )
        pincode = address.get("postcode", "")
        formatted = result.get("display_name", "").replace(", India", "")
        key = formatted.lower()
        if formatted and key not in seen:
            seen.add(key)
            suggestions.append({
                "label": formatted,
                "address": formatted,
                "city": city,
                "district": district,
                "state": state,
                "pincode": pincode,
            })
    return suggestions


def pincode_suggestions(pincode):
    request = Request(
        INDIA_POST_PINCODE_URL + "/" + quote(pincode),
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urlopen(request, timeout=12) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, list) or not data or data[0].get("Status") != "Success":
        return [], "NOT_FOUND"

    suggestions = []
    seen = set()
    for office in data[0].get("PostOffice") or []:
        name = str(office.get("Name", "")).strip()
        district = str(office.get("District", "")).strip()
        state = str(office.get("State", "")).strip()
        key = (name, district, state)
        if key in seen or not name:
            continue
        seen.add(key)
        suggestions.append({
            "label": ", ".join(part for part in (name, district, state) if part),
            "address": name,
            "city": name,
            "district": district,
            "state": state,
            "pincode": pincode,
        })
    return suggestions, "OK"


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

    def do_GET(self):
        if self.path.startswith("/api/pincode/"):
            pincode = self.path.split("/api/pincode/", 1)[1].split("?", 1)[0].strip()
            if not pincode.isdigit() or len(pincode) != 6:
                self._json(400, {"success": False, "error": "A valid 6-digit pincode is required."})
                return
            try:
                suggestions, status = pincode_suggestions(pincode)
                if status == "NOT_FOUND" or not suggestions:
                    self._json(404, {"success": False, "error": "No locality was found for this pincode."})
                else:
                    self._json(200, {"success": True, "suggestions": suggestions})
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, KeyError, TypeError):
                self._json(502, {"success": False, "error": "Pincode lookup failed"})
            return
        if self.path.startswith("/api/address-suggestions"):
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            search = parse_qs(query).get("q", [""])[0].strip()
            if len(search) < 3:
                self._json(200, {"success": True, "suggestions": []})
                return
            api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
            if not api_key:
                self._json(503, {"success": False, "error": "Address suggestions are not configured"})
                return
            try:
                suggestions, status = address_suggestions(search, api_key)
                if status not in ("OK", "ZERO_RESULTS", "FALLBACK"):
                    self._json(502, {"success": False, "error": "Address suggestions are unavailable"})
                else:
                    self._json(200, {"success": True, "suggestions": suggestions})
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, KeyError, TypeError):
                self._json(502, {"success": False, "error": "Address suggestions lookup failed"})
            return
        if self.path.startswith("/api/"):
            self._json(405, {"success": False, "error": "Use POST for delivery distance requests."})
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
            origin = str(request_data.get("origin", "")).strip()
            destination = str(request_data.get("destination", "")).strip()
            if not origin or not destination:
                raise ValueError("Origin and destination are required")

            origin_location, _ = geocode(origin, api_key)
            destination_location, _ = geocode(destination, api_key)

            routes_request = json.dumps({
                "origin": {"location": {"latLng": origin_location}} if origin_location else {"address": origin},
                "destination": {"location": {"latLng": destination_location}} if destination_location else {"address": destination},
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
                self._json(422, {"success": False, "error": "Driving distance could not be calculated for this address."})
                return
            distance_km = round(float(routes[0]["distanceMeters"]) / 1000, 1)
            delivery_charge = round(max(0, distance_km - 2) * 25)
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