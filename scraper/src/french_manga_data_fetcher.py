import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime
import glob
import mysql.connector
from dotenv import load_dotenv
import os
import logging

load_dotenv()
BASE_URL = os.getenv("BASE_URL", "https://w14.french-manga.net")
MANGA_LIST_URL_PATTERN = os.getenv("MANGA_LIST_URL_PATTERN", BASE_URL + "/index.php?cstart={cstart}&do=cat&category=manga-streaming-1")

# Configuration du logger pour tout afficher dans logs/cinema_scraper.log
log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', 'logs', 'cinema_scraper.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def get_all_main_pages():
    items = []
    cstart = 1
    last_first_id = None
    while True:
        url = MANGA_LIST_URL_PATTERN.format(cstart=cstart)
        response = requests.get(url)
        if response.status_code != 200:
            break
        soup = BeautifulSoup(response.text, "html.parser")
        # Vérifier la présence du message d'arrêt
        berrors = soup.find("div", class_="berrors")
        if berrors and 'Aucun Film/Série trouvé' in berrors.get_text():
            break
        page_items = []
        for a in soup.find_all("a", href=re.compile(r"newsid=\d+")):
            link = a["href"]
            if not link.startswith("http"):
                link = BASE_URL + "/" + link.lstrip("/")
            title = a.get_text(strip=True)
            # Chercher la version dans le lien précédent (VF, VOSTFR, etc.)
            version = None
            prev = a.find_previous_sibling("a")
            if prev:
                version = prev.get_text(strip=True)
            # Chercher EPS dans la balise <span class="mli-eps"> <i>...</i> </span> la plus proche
            eps = None
            parent = a.find_parent()
            eps_tag = None
            while parent and not eps_tag:
                eps_tag = parent.find("span", class_="mli-eps")
                parent = parent.find_parent() if parent else None
            if eps_tag:
                i_tag = eps_tag.find("i")
                if i_tag:
                    eps = i_tag.get_text(strip=True)
            page_items.append({
                "short-title": title,
                "version": version,
                "eps": eps,
                "link": link
            })
        # Stopper si la page ne contient aucun nouvel élément ou si on boucle sur la même page
        if not page_items:
            break
        # Détection de boucle infinie : si le premier id de la page est le même que la précédente
        first_id_match = re.search(r"newsid=(\d+)", page_items[0]["link"]) if page_items else None
        first_id = first_id_match.group(1) if first_id_match else None
        if first_id and first_id == last_first_id:
            break
        last_first_id = first_id
        items.extend(page_items)
        cstart += 1
    return items

def get_details_page(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, "html.parser")
    # Récupérer le short-title depuis <div class="fmid"><h1 id="s-title">...</h1></div>
    short_title = None
    fmid = soup.find("div", class_="fmid")
    if fmid:
        h1 = fmid.find("h1", id="s-title")
        if h1:
            short_title = h1.get_text(strip=True)
    # Récupérer le genre sous forme de tableau depuis <li><span>Genre : </span> ... </li>
    genre = []
    description = None
    for li in soup.find_all("li"):
        span = li.find("span")
        # Genre
        if span and "Genre" in span.get_text():
            genre_text = li.get_text().replace(span.get_text(), "").strip()
            genre = [g.strip() for g in re.split(r"-|/", genre_text) if g.strip()]
        # Description
        desc_div = li.find("div", id="s-desc")
        if desc_div:
            description = desc_div.get_text(" ", strip=True)
    # Récupérer l'image principale (première image non avatar)
    image = None
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if "noavatar" not in src and src.endswith((".jpg", ".jpeg", ".png")):
            image = src
            break
    # Récupérer les épisodes par version (VF, VOSTFR, etc.)
    episodes_by_version = {}
    # On cherche tous les blocs de version (ex: <div class="VF-tab"> ou <div class="VOSTFR-tab">)
    for tab in soup.find_all("div", class_=re.compile(r"(VF|VOSTFR)-tab")):
        version = tab.get_text(strip=True)
        elink = tab.find_next_sibling("div", class_="elink")
        if not elink:
            continue
        # On cherche le bloc qui contient les liens d'épisodes
        fdesc = elink.find("div", class_=re.compile(r"fdesc"))
        episodes = []
        if fdesc:
            for a in fdesc.find_all("a", class_="fstab"):
                episodes.append({
                    "id": a.get("id"),
                    "title": a.get("title", a.get_text(strip=True)),
                    "href": a.get("href")
                })
        episodes_by_version[version] = episodes
    return {
        "short-title": short_title,
        "genre": genre,
        "description": description,
        "image": image,
        "episodes": episodes_by_version
    }

def save_partial(data, filename):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def save_to_mysql(data, host=None, user=None, password=None, database=None, table="mangas"):
    # Charger les variables d'environnement si non fournies
    if host is None:
        host = os.getenv("MYSQL_HOST", "localhost")
    if user is None:
        user = os.getenv("MYSQL_USER", "root")
    if password is None:
        password = os.getenv("MYSQL_PASSWORD", "")
    if database is None:
        database = os.getenv("MYSQL_DATABASE", "manga_db")

    # Statistiques pour le rapport d'importation
    stats = {
        "total": len(data),
        "inserted": 0,
        "skipped": 0,
        "errors": 0
    }

    try:
        conn = mysql.connector.connect(
            host=host,
            user=user,
            password=password,
            database=database
        )
        cursor = conn.cursor()
        logger.info(f"Connexion à MySQL réussie pour la migration dans la table {table}.")
        # Création de la table si elle n'existe pas, avec id unique
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {table} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                short_title TEXT,
                genre TEXT,
                description TEXT,
                image TEXT,
                eps VARCHAR(32),
                version TEXT,
                episodes JSON,
                UNIQUE KEY unique_id (id)
            )
        ''')
        logger.info(f"Table {table} vérifiée/créée.")

        # Pour chaque élément dans les données
        for item in data:
            try:
                # Vérifier si l'ID existe
                if not item.get("id"):
                    logger.warning("Élément ignoré : ID manquant")
                    stats["skipped"] += 1
                    continue

                # Vérifier les données essentielles
                if not item.get("short-title") or not item.get("episodes"):
                    logger.warning(f"Élément {item.get('id', 'inconnu')} ignoré : données essentielles manquantes")
                    stats["skipped"] += 1
                    continue

                # Insérer ou mettre à jour l'entrée selon id unique
                cursor.execute(f'''
                    INSERT INTO {table} (id, short_title, genre, description, image, eps, version, episodes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        short_title=VALUES(short_title),
                        genre=VALUES(genre),
                        description=VALUES(description),
                        image=VALUES(image),
                        eps=VALUES(eps),
                        version=VALUES(version),
                        episodes=VALUES(episodes)
                ''', (
                    int(item.get("id")),
                    item.get("short-title"),
                    ", ".join(item.get("genre", [])),
                    item.get("description"),
                    item.get("image"),
                    item.get("eps") if isinstance(item.get("eps"), str) else json.dumps(item.get("eps")),
                    ", ".join(item.get("version", [])),
                    json.dumps(item.get("episodes", {}))
                ))
                logger.info(f"Manga id={item.get('id')} migré/MAJ avec succès.")
                stats["inserted"] += 1

            except Exception as e:
                logger.error(f"Erreur lors de l'insertion de l'élément {item.get('id', 'inconnu')}: {str(e)}")
                stats["errors"] += 1
                continue

        conn.commit()
        cursor.close()
        conn.close()
        logger.info(f"Migration terminée : {stats['inserted']} insérés, {stats['skipped']} ignorés, {stats['errors']} erreurs.")
        # Afficher le rapport d'importation
        print("\nRapport d'importation:")
        print(f"Total d'éléments traités: {stats['total']}")
        print(f"Éléments insérés avec succès: {stats['inserted']}")
        print(f"Éléments ignorés: {stats['skipped']}")
        print(f"Erreurs rencontrées: {stats['errors']}")

    except mysql.connector.Error as err:
        logger.error(f"Erreur de connexion à MySQL: {err}")
        raise

def main():
    # Charger tous les ids déjà scrapés dans les fichiers manga-h-*-d-*.json
    scraped_ids = set()
    for fname in glob.glob("manga-h-*-d-*.json"):
        try:
            with open(fname, "r", encoding="utf-8") as f:
                for entry in json.load(f):
                    if "id" in entry:
                        scraped_ids.add(entry["id"])
        except Exception:
            pass
    now = datetime.now()
    filename = f"manga-h-{now.strftime('%H-%M-%S')}-d-{now.strftime('%d-%m-%Y')}.json"
    data = []
    items = get_all_main_pages()
    logger.info("Début du scraping des mangas.")
    try:
        for item in items:
            # Extraire l'id depuis le lien de détail (newsid=XXXX)
            newsid = None
            match = re.search(r"newsid=(\d+)", item["link"])
            if match:
                newsid = match.group(1)
            # Vérifier si déjà scrapé
            if newsid in scraped_ids:
                logger.info(f"Manga déjà scrapé (id={newsid}), ignoré.")
                continue
            logger.info(f"Scraping détails manga id={newsid} : {item['short-title']}")
            details = get_details_page(item["link"])
            item.update(details)
            episodes = details.get("episodes", {})
            version_list = []
            total_episodes = sum(len(episodes[v]) for v in episodes if episodes[v])
            for version in ["VF", "VOSTFR"]:
                if version in episodes and episodes[version]:
                    version_list.append(version)
            # Si un seul épisode au total, eps = 'FILMS'
            if total_episodes == 1:
                item["eps"] = "FILMS"
            item["version"] = version_list
            # Réorganiser l'ordre des champs : id, short-title, ...
            ordered_item = {"id": newsid}
            ordered_item.update({k: item[k] for k in item if k != "id"})
            data.append(ordered_item)
            # Sauvegarde partielle après chaque ajout
            save_partial(data, filename)
    except (KeyboardInterrupt, Exception) as e:
        logger.error(f"Interruption ou erreur détectée : {e}")
        save_partial(data, filename)
        logger.info(f"Données sauvegardées dans {filename} avant l'arrêt.")
        raise
    # Sauvegarde finale
    save_partial(data, filename)
    logger.info(f"Scraping terminé. Données sauvegardées dans {filename}")

if __name__ == "__main__":
    load_dotenv()
    main()
    
    # Traitement de tous les fichiers JSON
    print("\nDébut de l'importation des fichiers JSON vers MySQL...")
    import glob
    
    # Récupérer tous les fichiers JSON
    json_files = sorted(glob.glob("manga-h-*-d-*.json"))
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
                
                # Importation vers MySQL
                save_to_mysql(
                    data,
                    host=os.getenv("MYSQL_HOST", "localhost"),
                    user=os.getenv("MYSQL_USER", "root"),
                    password=os.getenv("MYSQL_PASSWORD", ""),
                    database=os.getenv("MYSQL_DATABASE", "manga_db")
                )
                print(f"Importation réussie pour {file}")
                
            except Exception as e:
                print(f"Erreur lors du traitement de {file}: {str(e)}")
                continue
        
        print("\nTraitement de tous les fichiers JSON terminé.")