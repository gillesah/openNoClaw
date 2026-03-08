#!/usr/bin/env python3
"""
setup_youtube_auth.py — Auth YouTube pour Gulliver (one-shot, en local).

Réutilise les credentials OAuth Google déjà configurés pour Gmail.
À lancer UNE SEULE FOIS sur ta machine locale.

Prérequis (une seule chose à faire dans Google Cloud Console) :
  1. https://console.cloud.google.com → projet polynomial-net-188321
  2. APIs et services → Identifiants → ton client OAuth Web
  3. Ajouter dans "URI de redirection autorisées" : http://localhost:8090
  4. Enregistrer

Ensuite, lance simplement :
  python3 setup_youtube_auth.py

Le script récupère les credentials depuis le serveur, fait l'auth, et redéploie le token.
"""

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

REDIRECT_URI = "http://localhost:8090"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
SERVER_DATA_PATH = "gulliver:/home/gillesah/openNoClaw/data"
GMAIL_JSON = "/tmp/gmail_gilles_tmp.json"
TOKEN_OUT = "/tmp/youtube-token.json"

auth_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        auth_code = params.get("code")
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<h2>✅ Autorisé ! Vous pouvez fermer cet onglet.</h2>".encode())

    def log_message(self, *args):
        pass


def run():
    # 1. Récupérer les credentials Gmail depuis le serveur
    print("📥 Récupération des credentials OAuth depuis gulliver...")
    r = subprocess.run(
        ["scp", f"{SERVER_DATA_PATH}/gmail_gilles.json", GMAIL_JSON],
        capture_output=True
    )
    if r.returncode != 0:
        print(f"❌ Impossible de récupérer gmail_gilles.json : {r.stderr.decode()}")
        print("   Assure-toi que ssh gulliver fonctionne.")
        sys.exit(1)

    with open(GMAIL_JSON) as f:
        creds = json.load(f)
    os.remove(GMAIL_JSON)

    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")
    if not client_id or not client_secret:
        print("❌ gmail_gilles.json ne contient pas client_id/client_secret")
        sys.exit(1)

    print(f"   Client ID: {client_id[:40]}...")

    # 2. Construire l'URL d'auth YouTube
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urllib.parse.urlencode({
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
        })
    )

    print(f"\n🔗 Ouverture du navigateur pour autoriser YouTube sur la chaîne FluenzrApp...")
    webbrowser.open(auth_url)

    print("⏳ En attente de l'autorisation sur http://localhost:8090 ...")
    server = HTTPServer(("localhost", 8090), CallbackHandler)
    server.handle_request()

    if not auth_code:
        print("❌ Pas de code reçu. Réessaie.")
        sys.exit(1)

    print("✅ Code reçu, échange contre un token...")

    # 3. Échanger le code → tokens
    data = urllib.parse.urlencode({
        "code": auth_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()

    resp = json.loads(urllib.request.urlopen(
        "https://oauth2.googleapis.com/token", data
    ).read())

    if "refresh_token" not in resp:
        print(f"❌ Pas de refresh_token. Réponse : {resp}")
        print("\n   Si c'est une erreur 'redirect_uri_mismatch' :")
        print("   → Ajoute http://localhost:8090 dans les URIs autorisées du client OAuth")
        print("   → console.cloud.google.com → APIs → Identifiants → client OAuth Web")
        sys.exit(1)

    token_data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": resp["refresh_token"],
        "scope": SCOPE,
    }

    with open(TOKEN_OUT, "w") as f:
        json.dump(token_data, f, indent=2)
    os.chmod(TOKEN_OUT, 0o600)

    print(f"   Token sauvegardé localement : {TOKEN_OUT}")

    # 4. Déployer sur le serveur
    print(f"\n📤 Déploiement sur le serveur Gulliver...")
    r = subprocess.run(
        ["scp", TOKEN_OUT, f"{SERVER_DATA_PATH}/youtube-token.json"],
        capture_output=True
    )
    if r.returncode != 0:
        print(f"❌ Déploiement échoué : {r.stderr.decode()}")
        print(f"   Lance manuellement : scp {TOKEN_OUT} {SERVER_DATA_PATH}/youtube-token.json")
        sys.exit(1)

    os.remove(TOKEN_OUT)
    print("✅ Token YouTube déployé sur gulliver !")
    print("\n🎬 Gulliver peut maintenant uploader des vidéos sur la chaîne FluenzrApp.")
    print("   Teste avec : --youtube flag dans make_video.py")


if __name__ == "__main__":
    run()
