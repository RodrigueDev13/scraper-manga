import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime
import glob
import mysql.connector
from dotenv import load_dotenv
import os
import time

load_dotenv()
BASE_URL = os.getenv("FS_MIRROR_BASE_URL", "https://fsmirror84.lol")
FILMS_URL = f"{BASE_URL}/films/"
SERIES_URL = f"{BASE_URL}/s-tv/"

# ----------- Scraping helpers -----------
def get_soup(url):
    resp = requests.get(url)
    if resp.status_code != 200:
        return None
    return BeautifulSoup(resp.text, "html.parser")

def get_all_pages(section_url, section_type):
    items = []
    page = 1
    while True:
        if page == 1:
            url = section_url
        else:
            url = f"{section_url}page/{page}/"
        soup = get_soup(url)
        if not soup:
            print(f"[DEBUG] Aucune réponse pour {url}")
            break
        # DEBUG: Afficher un extrait du HTML pour vérifier la structure
        print(f"[DEBUG] HTML page {page} (début):\n", soup.prettify()[:2000])
        blocks = soup.find_all("div", class_="short serie")
        if not blocks:
            print(f"[DEBUG] Aucun bloc 'short serie' trouvé sur {url}")
            break
        for block in blocks:
            link_tag = block.find("a", href=True)
            if not link_tag:
                continue
            item_url = link_tag["href"]
            if not item_url.startswith("http"):
                # Correction pour les liens relatifs
                if item_url.startswith("/films/") or item_url.startswith("/s-tv/"):
                    item_url = BASE_URL + item_url
                else:
                    item_url = BASE_URL + "/" + item_url.lstrip("/")
            # ID extraction (corrigé pour correspondre à l'URL réelle)
            id_match = re.search(r"/(\d+)-", item_url)
            if not id_match:
                print(f"[DEBUG] ID non trouvé dans l'URL: {item_url}")
                continue
            item_id = id_match.group(1)
            # Image
            img_tag = block.find("div", class_="short-poster")
            image = ""
            if img_tag:
                img = img_tag.find("img")
                if img:
                    image = img.get("src", "")
            # Titre
            title_tag = block.find("div", class_="short-title")
            title = title_tag.get_text(strip=True) if title_tag else ""
            # Version (VF/VOSTFR...)
            version_tag = block.find("div", class_="film-version")
            version = []
            if version_tag:
                version = [a.get_text(strip=True) for a in version_tag.find_all("a")]
            # Nombre d'épisodes (pour séries)
            eps = "films" if section_type == "films" else "series"
            items.append({
                "id": item_id,
                "title": title,
                "version": version,
                "eps": eps,
                "link": item_url,
                "image": image
            })
        page += 1
    return items

def get_item_details(url, section_type):
    soup = get_soup(url)
    if not soup:
        return {}
    # Titre principal
    title = ""
    title_element = soup.find("h1", id="s-title")
    if title_element:
        title = title_element.get_text(strip=True).split("(")[0].strip()
    # Titre original
    title_original = ""
    meta_title = soup.find("li", string=re.compile("Titre original", re.I))
    if meta_title:
        title_original = meta_title.get_text(strip=True).split(":",1)[-1].strip()
    # Genres
    genres = []
    genres_span = soup.find("span", class_="genres")
    if genres_span:
        for genre_link in genres_span.find_all("a"):
            genres.append(genre_link.get_text(strip=True))
    # Réalisateur
    realisateur = ""
    meta_realisateur = soup.find("li", string=re.compile("Réalisateur", re.I))
    if meta_realisateur:
        realisateur = meta_realisateur.get_text(strip=True).split(":",1)[-1].strip()
    # Date de sortie
    date_sortir = ""
    date_span = soup.find("span", class_="tag release_date")
    if date_span:
        date_match = re.search(r"(\d{4})", date_span.get_text())
        if date_match:
            date_sortir = date_match.group(1)
    # Langue d'origine
    langueo = ""
    meta_langue = soup.find("li", string=re.compile("Langue d'origine", re.I))
    if meta_langue:
        langueo = meta_langue.get_text(strip=True).split(":",1)[-1].strip()
    # Acteurs
    acteurs = []
    actors_li = soup.find("li", string=re.compile("Acteurs", re.I))
    if actors_li:
        for actor_link in actors_li.find_all("a"):
            acteurs.append({
                "nom": actor_link.get_text(strip=True),
                "image": "" # Peut être enrichi via TMDB si besoin
            })
    # Description
    description = ""
    fdesc = soup.find("div", id="s-desc")
    if fdesc:
        description = fdesc.get_text(strip=True)
    # Image principale
    image = ""
    fposter = soup.find("div", class_="fposter")
    if fposter:
        img = fposter.find("img", class_="dvd-thumbnail")
        if img:
            image = img.get("src", "")
    # Episodes/streams
    episodes = {}
    player_options = soup.find("div", id="player-options")
    if player_options:
        for button in player_options.find_all("button", class_="player-option"):
            server_name = button.get("data-player", "").strip()
            default_url = button.get("data-url-default", "")
            if not server_name:
                server_name = button.get_text(strip=True)
            if server_name:
                episodes[server_name] = {}
                version_dropdown = button.find("div", class_="version-dropdown")
                if version_dropdown:
                    for version_option in version_dropdown.find_all("div", class_="version-option"):
                        version = version_option.get("data-version", "").strip()
                        url = version_option.get("data-url", "").strip()
                        if version == "VFQ":
                            version = "VF"
                        elif version_option.get_text(strip=True) == "FRENCH":
                            version = "VF"
                        if version and url:
                            if version not in episodes[server_name]:
                                episodes[server_name][version] = []
                            episodes[server_name][version].append({"href": url})
                        elif version and default_url:
                            if version not in episodes[server_name]:
                                episodes[server_name][version] = []
                            episodes[server_name][version].append({"href": default_url})
    return {
        "title": title,
        "title_original": title_original,
        "genre": genres,
        "realisateur": realisateur,
        "date_sortir": date_sortir,
        "langueo": langueo,
        "acteurs": acteurs,
        "description": description,
        "image": image,
        "episodes": episodes,
        "eps": "films" if section_type == "films" else "series"
    }

def save_partial(data, filename):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def save_to_mysql(data, host=None, user=None, password=None, database=None, table="fsmirror"):
    if host is None:
        host = os.getenv("MYSQL_HOST", "localhost")
    if user is None:
        user = os.getenv("MYSQL_USER", "root")
    if password is None:
        password = os.getenv("MYSQL_PASSWORD", "")
    if database is None:
        database = os.getenv("MYSQL_DATABASE", "cinema_db")
    stats = {"total": len(data), "inserted": 0, "skipped": 0, "errors": 0}
    try:
        conn = mysql.connector.connect(host=host, user=user, password=password, database=database)
        cursor = conn.cursor()
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {table} (
                id VARCHAR(32) PRIMARY KEY,
                title TEXT,
                title_original TEXT,
                version TEXT,
                eps VARCHAR(32),
                link TEXT,
                genre TEXT,
                realisateur TEXT,
                date_sortir VARCHAR(16),
                langueo TEXT,
                acteurs JSON,
                description TEXT,
                image TEXT,
                episodes JSON
            )
        ''')
        for item in data:
            try:
                if not item.get("id"):
                    print(f"Élément ignoré : ID manquant")
                    stats["skipped"] += 1
                    continue
                cursor.execute(f'''
                    REPLACE INTO {table} (
                        id, title, title_original, version, eps, link, genre, realisateur, date_sortir, langueo, acteurs, description, image, episodes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    str(item.get("id")),
                    item.get("title"),
                    item.get("title_original"),
                    json.dumps(item.get("version", [])),
                    item.get("eps"),
                    item.get("link"),
                    json.dumps(item.get("genre", [])),
                    item.get("realisateur"),
                    item.get("date_sortir"),
                    item.get("langueo"),
                    json.dumps(item.get("acteurs", [])),
                    item.get("description"),
                    item.get("image"),
                    json.dumps(item.get("episodes", {}))
                ))
                stats["inserted"] += 1
            except Exception as e:
                print(f"Erreur lors de l'insertion de l'élément {item.get('id', 'inconnu')}: {str(e)}")
                stats["errors"] += 1
                continue
        conn.commit()
        cursor.close()
        conn.close()
        print("\nRapport d'importation:")
        print(f"Total d'éléments traités: {stats['total']}")
        print(f"Éléments insérés avec succès: {stats['inserted']}")
        print(f"Éléments ignorés: {stats['skipped']}")
        print(f"Erreurs rencontrées: {stats['errors']}")
    except mysql.connector.Error as err:
        print(f"Erreur de connexion à MySQL: {err}")
        raise

def main():
    now = datetime.now()
    filename = f"fsmirror-h-{now.strftime('%H-%M-%S')}-d-{now.strftime('%d-%m-%Y')}.json"
    data = []
    # Films
    print("Scraping des films...")
    films = get_all_pages(FILMS_URL, "films")
    for film in films:
        details = get_item_details(film["link"], "films")
        film.update(details)
        data.append(film)
        save_partial(data, filename)
    # Séries
    print("Scraping des séries...")
    series = get_all_pages(SERIES_URL, "series")
    for serie in series:
        details = get_item_details(serie["link"], "series")
        serie.update(details)
        data.append(serie)
        save_partial(data, filename)
    save_partial(data, filename)
    print(f"Scraping terminé. Données sauvegardées dans {filename}")

if __name__ == "__main__":
    load_dotenv()
    main()
    print("\nDébut de l'importation des fichiers JSON vers MySQL...")
    json_files = sorted(glob.glob("fsmirror-h-*-d-*.json"))
    total_files = len(json_files)
    if total_files == 0:
        print("Aucun fichier JSON trouvé à importer.")
    else:
        print(f"Nombre total de fichiers à traiter : {total_files}")
        for index, file in enumerate(json_files, 1):
            print(f"\nTraitement du fichier {index}/{total_files} : {file}")
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                print(f"Nombre d'éléments dans {file} : {len(data)}")
                save_to_mysql(
                    data,
                    host=os.getenv("MYSQL_HOST", "localhost"),
                    user=os.getenv("MYSQL_USER", "root"),
                    password=os.getenv("MYSQL_PASSWORD", ""),
                    database=os.getenv("MYSQL_DATABASE", "cinema_db")
                )
                print(f"Importation réussie pour {file}")
            except Exception as e:
                print(f"Erreur lors du traitement de {file}: {str(e)}")
                continue
        print("\nTraitement de tous les fichiers JSON terminé.")
