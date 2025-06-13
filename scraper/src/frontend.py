import sys; print(sys.executable)
from flask import Flask, render_template, request, jsonify, send_from_directory
import threading
import subprocess
import glob
import os

app = Flask(__name__)

SCRAPER_SCRIPT = os.path.join(os.path.dirname(__file__), 'french_manga_data_fetcher.py')
SCRAPING_THREAD = None
SCRAPING_STOP = threading.Event()

# Utilitaire pour lister les 5 derniers fichiers JSON
def get_last_json_files():
    files = sorted(glob.glob(os.path.join(os.path.dirname(__file__), '../../manga-h-*-d-*.json')), key=os.path.getmtime, reverse=True)
    return [os.path.basename(f) for f in files[:5]]

# Thread target pour le scraping
def run_scraper():
    SCRAPING_STOP.clear()
    # Utilise le même interpréteur Python que Flask (celui du venv)
    proc = subprocess.Popen([sys.executable, SCRAPER_SCRIPT])
    while proc.poll() is None:
        if SCRAPING_STOP.is_set():
            proc.terminate()
            print('\n[INFO] Scraping arrêté par l\'utilisateur.\n')
            break
    SCRAPING_STOP.clear()

@app.route('/')
def dashboard():
    last_files = get_last_json_files()
    return render_template('dashboard.html', last_files=last_files)

@app.route('/start_scraping', methods=['POST'])
def start_scraping():
    global SCRAPING_THREAD
    if SCRAPING_THREAD and SCRAPING_THREAD.is_alive():
        return jsonify({'status': 'already_running'})
    SCRAPING_THREAD = threading.Thread(target=run_scraper)
    SCRAPING_THREAD.start()
    return jsonify({'status': 'started'})

@app.route('/stop_scraping', methods=['POST'])
def stop_scraping():
    SCRAPING_STOP.set()
    return jsonify({'status': 'stopping'})

@app.route('/migrate', methods=['POST'])
def migrate():
    filename = request.form.get('filename')
    # Vérifie que le fichier est bien dans la liste des fichiers JSON scrapés
    allowed_files = set(get_last_json_files())
    if not filename or not filename.endswith('.json') or filename not in allowed_files:
        return jsonify({'status': 'error', 'message': 'Nom de fichier invalide ou non autorisé'})
    filepath = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../', filename))
    if not os.path.exists(filepath):
        return jsonify({'status': 'error', 'message': 'Fichier non trouvé'})
    try:
        result = subprocess.run([
            sys.executable, '-c',
            f"import sys; sys.path.append('scraper/src'); from french_manga_data_fetcher import save_to_mysql; import json; data=json.load(open(r'{filepath}', encoding='utf-8')); save_to_mysql(data)"
        ], capture_output=True, text=True)
        if result.returncode == 0:
            return jsonify({'status': 'success', 'output': result.stdout})
        else:
            return jsonify({'status': 'error', 'message': result.stderr or result.stdout})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/last_files', methods=['GET'])
def last_files():
    return jsonify({'files': get_last_json_files()})

@app.route('/logs')
def logs():
    log_path = os.path.join(os.path.dirname(__file__), '../../logs/cinema_scraper.log')
    try:
        with open(log_path, encoding='utf-8') as f:
            content = f.read()[-10000:]  # Limite à 10k caractères pour éviter surcharge
        return jsonify({'logs': content})
    except Exception as e:
        return jsonify({'logs': f'Erreur lecture logs: {e}'})

@app.route('/delete_file', methods=['POST'])
def delete_file():
    data = request.get_json()
    filename = data.get('filename')
    if not filename or not filename.startswith('manga-h-') or not filename.endswith('.json'):
        return jsonify({'status': 'error', 'message': 'Nom de fichier non autorisé.'})
    file_path = os.path.join(os.getcwd(), filename)
    if not os.path.isfile(file_path):
        return jsonify({'status': 'error', 'message': 'Fichier introuvable.'})
    try:
        os.remove(file_path)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

if __name__ == '__main__':
    app.run(debug=True)
