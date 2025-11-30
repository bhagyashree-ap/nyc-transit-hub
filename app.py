from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, session
import sqlite3
import csv
import requests
from flask_cors import CORS
from google.transit import gtfs_realtime_pb2
from functools import wraps


app = Flask(__name__)
CORS(app)


# ----------------------------
# DATABASE SETUP
# ----------------------------
def init_db():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS favorites
                 (id INTEGER PRIMARY KEY, station TEXT, route TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS alerts
                 (id INTEGER PRIMARY KEY, station TEXT, route TEXT, alert_msg TEXT)''')
    conn.commit()
    conn.close()


init_db()


# ----------------------------
# PUBLIC API ENDPOINTS
# ----------------------------
SUBWAY_FEEDS = {
    "ACE": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-ace",
    "BDFMFS": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-bdfm",
    "G": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-g",
    "JZ": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-jz",
    "NQRW": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-nqrw",
    "L": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-l",
    "1234567S": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs",
    "SIR": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-si"
}

ELEVATOR_JSON_FEEDS = {
    "current_outages": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fnyct_ene.json",
}

SERVICE_ALERTS_JSON = {
    "all_alerts": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fall-alerts.json",
    "subway_alerts": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts.json",
    "bus_alerts": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fbus-alerts.json",
    "lirr_alerts": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Flirr-alerts.json",
    "mnr_alerts": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fmnr-alerts.json"
}


# ----------------------------
# ROUTES
# ----------------------------
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/dashboard.html')
def dashboard():
    return render_template('dashboard.html')


# ----------------------------
# API: Stations & Routes
# ----------------------------
@app.route('/api/stations')
def stations():
    stations_list = []
    try:
        with open('MTA_Subway_Stations.csv', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                routes = row['Daytime Routes'].split()
                for route in routes:
                    stations_list.append({
                        'stop_id': row['GTFS Stop ID'],
                        'station_name': row['Stop Name'],
                        'line': route,
                        'lat': row['GTFS Latitude'],
                        'lon': row['GTFS Longitude']
                    })
    except Exception as e:
        print("Error reading CSV:", e)
    return jsonify(stations_list)


# ----------------------------
# Preload station coordinates & names
# ----------------------------
station_coords = {}
station_names = {}
try:
    with open('MTA_Subway_Stations.csv', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            stop_id = row['GTFS Stop ID']
            lat = float(row['GTFS Latitude'])
            lon = float(row['GTFS Longitude'])
            name = row['Stop Name']
            station_coords[stop_id] = (lat, lon)
            station_names[stop_id] = name
except Exception as e:
    print("Error reading station CSV for coordinates:", e)


# ----------------------------
# REAL-TIME TRAINS API
# ----------------------------
@app.route('/api/realtime_trains/<line>')
def realtime_trains(line):
    url = SUBWAY_FEEDS.get(line.upper())
    if not url:
        return jsonify([])

    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        feed.ParseFromString(resp.content)
        trains = []

        for entity in feed.entity:
            if entity.HasField("trip_update"):
                trip_id = entity.trip_update.trip.trip_id
                for stu in entity.trip_update.stop_time_update:
                    stop_id = stu.stop_id
                    arrival = stu.arrival.time if stu.HasField('arrival') else None
                    departure = stu.departure.time if stu.HasField('departure') else None
                    lat, lon = station_coords.get(stop_id, (None, None))
                    name = station_names.get(stop_id, stop_id)
                    trains.append({
                        "trip_id": trip_id,
                        "station": name,
                        "arrival": arrival,
                        "departure": departure,
                        "lat": lat,
                        "lon": lon
                    })
        return jsonify(trains)
    except Exception as e:
        print("Error fetching realtime trains:", e)
        return jsonify([])


# ----------------------------
# FAVORITES API
# ----------------------------
@app.route('/api/favorites', methods=['POST', 'GET', 'DELETE'])
def favorites():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    if request.method == 'POST':
        data = request.json
        station = data.get('station')
        route = data.get('route')
        c.execute("INSERT INTO favorites (station, route) VALUES (?,?)", (station, route))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Favorite added'})
    elif request.method == 'DELETE':
        data = request.json
        station = data.get('station')
        route = data.get('route')
        c.execute("DELETE FROM favorites WHERE station=? AND route=?", (station, route))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Favorite removed'})
    else:
        c.execute("SELECT station, route FROM favorites")
        favs = c.fetchall()
        conn.close()
        return jsonify([{'station': f[0], 'route': f[1]} for f in favs])


# ----------------------------
# ACCESSIBILITY API
# ----------------------------
@app.route('/get_accessibility')
def get_accessibility():
    try:
        resp = requests.get(ELEVATOR_JSON_FEEDS['current_outages'])
        data = resp.json()
        cleaned = []
        for o in data:
            cleaned.append({
                'station': o.get('station', ''),
                'equipment': o.get('equipment', ''),
                'type': o.get('equipmenttype') or o.get('equipmentType', ''),
                'accessible': 'Yes' if o.get('ADA', 'N') == 'Y' else 'No',
                'reason': o.get('reason', ''),
                'return_time': o.get('estimatedreturntoservice', ''),
                'line': o.get('trainno', '')
            })
        return jsonify({'accessibility': cleaned})
    except:
        return jsonify({'accessibility': []})


# ----------------------------
# SERVICE ALERTS API
# ----------------------------
@app.route('/api/alerts/<alert_type>')
def service_alerts(alert_type):
    feed_url = SERVICE_ALERTS_JSON.get(alert_type)
    if not feed_url:
        return jsonify([])

    try:
        resp = requests.get(feed_url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        alerts_list = []

        for entity in data.get('entity', []):
            alert_info = entity.get('alert', {})

            # Extract text
            text = ""
            header_translations = alert_info.get('header_text', {}).get('translation', [])
            desc_translations = alert_info.get('description_text', {}).get('translation', [])

            for t in header_translations:
                if t.get("language") == "en" and t.get("text"):
                    text = t.get("text")
                    break
            if not text:
                for t in header_translations:
                    if t.get("language") == "en-html" and t.get("text"):
                        text = t.get("text")
                        break
            if not text:
                for t in desc_translations:
                    if t.get("language") == "en" and t.get("text"):
                        text = t.get("text")
                        break

            alerts_list.append({
                "id": entity.get("id"),
                "alert_type": alert_info.get('transit_realtime.mercury_alert', {}).get("alert_type"),
                "text": text,
                "routes": [e.get("route_id") for e in alert_info.get("informed_entity", []) if e.get("route_id")],
                "stops": [e.get("stop_id") for e in alert_info.get("informed_entity", []) if e.get("stop_id")],
                "start_time": alert_info.get("active_period", [{}])[0].get("start"),
                "created_at": alert_info.get('transit_realtime.mercury_alert', {}).get("created_at"),
                "updated_at": alert_info.get('transit_realtime.mercury_alert', {}).get("updated_at")
            })

        alerts_list.sort(key=lambda a: a.get('start_time', 0))
        return jsonify(alerts_list)

    except Exception as e:
        print("Error fetching alerts:", e)
        return jsonify([])


# ----------------------------
# MULTILINGUAL SUPPORT
# ----------------------------
translations = {
    'en': {
        'welcome': 'Welcome to NYC Transit Hub',
        'dashboard': 'Dashboard',
        'favorites': 'Favorites',
        'add_favorite': 'Add Favorite',
        'your_favorites': 'Your Favorites:',
        'accessibility_info': 'Accessibility Info',
        'filter_accessible': 'Filter by Accessibility',
        'filter_line': 'Filter by Line',
        'filter_station': 'Filter by Station',
        'apply_filters': 'Apply Filters',
        'service_alerts': 'Service Alerts',
        'alert_type': 'Alert Type',
        'transit_map': 'Transit Map'
    }
}


@app.route('/api/translate')
def translate():
    lang = request.args.get('lang', 'en')
    return jsonify(translations.get(lang, translations['en']))


# ----------------------------
# SIMPLE AUTH PAGES (NO FIREBASE)
# ----------------------------
@app.route('/signup')
def signup():
    return render_template("signup.html")


@app.route('/login')
def login():
    return render_template("login.html")


@app.route('/logout')
def logout():
    session.pop('user', None)
    flash("Logged out!", "info")
    return redirect(url_for('login'))


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            flash("Please login first.", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


app.secret_key = 'leetcodeapp'  # any random string


# ----------------------------
# RUN APP
# ----------------------------
if __name__ == '__main__':
    app.run(debug=True, port=5090)
