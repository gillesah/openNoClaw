# Make Video — Gulliver Video Factory

**IMPORTANT** : Tu PEUX créer des vidéos. Tu n'as pas besoin de "voir l'écran" toi-même.
Tu génères un script JSON, puis tu lances un script Python (`make_video.py`) qui fait tout le travail :
Playwright enregistre le navigateur headless, edge-tts génère la voix off, FFmpeg assemble le MP4.
Ton rôle = créer le script + lancer la commande shell + rapporter le résultat.

Quand l'utilisateur demande "crée une vidéo démo", JAMAIS répondre "je ne peux pas". Tu peux.

## Dépendances

Vérifie que tout est installé avant de commencer :

```bash
# FFmpeg
ffmpeg -version 2>/dev/null | head -1 || echo "MANQUANT: apt-get install -y ffmpeg"

# edge-tts
python3 -c "import edge_tts; print('edge-tts OK')" 2>/dev/null || \
  pip install edge-tts --quiet && echo "edge-tts installé"

# Playwright
python3 -c "from playwright.async_api import async_playwright; print('playwright OK')" 2>/dev/null || \
  echo "MANQUANT: playwright non disponible"
```

Si ffmpeg manque : `apt-get install -y ffmpeg` (dans le container, ou via `docker exec opennoclaw apt-get install -y ffmpeg`)
Si edge-tts manque : `pip install edge-tts`

## Voix disponibles (français)

| ID | Voix | Genre | Qualité |
|----|------|-------|---------|
| `fr-FR-DeniseNeural` | Denise | Féminine | ★★★★★ (recommandée) |
| `fr-FR-HenriNeural` | Henri | Masculine | ★★★★ |
| `fr-BE-CharlineNeural` | Charline | Féminine | ★★★★ |

## Format du script JSON

```json
{
  "title": "Titre de la vidéo",
  "voice": "fr-FR-DeniseNeural",
  "output": "/data/videos/nom-video.mp4",
  "scenes": [
    {
      "id": "intro",
      "type": "narrate",
      "text": "Texte pour la voix off.",
      "duration": 4
    },
    {
      "id": "open-app",
      "type": "navigate",
      "url": "https://app.fluenzr.io/login",
      "text": "Connectez-vous à Fluenzr.",
      "wait_ms": 3000
    },
    {
      "id": "click-btn",
      "type": "click",
      "selector": "#btn-login",
      "text": "Cliquez sur Se connecter.",
      "wait_ms": 2000
    },
    {
      "id": "fill-email",
      "type": "fill",
      "selector": "#email",
      "value": "demo@fluenzr.io",
      "text": "Saisissez votre adresse email.",
      "wait_ms": 1000
    },
    {
      "id": "scroll-down",
      "type": "scroll",
      "delta_y": 500,
      "text": "Faites défiler pour voir la suite.",
      "wait_ms": 1000
    },
    {
      "id": "pause",
      "type": "wait",
      "wait_ms": 2000
    }
  ]
}
```

### Types de scène

| type | Action | Champs requis |
|------|--------|---------------|
| `narrate` | Voix off uniquement, pas d'action navigateur | `text`, `duration` (sec) |
| `navigate` | Naviguer vers une URL | `url`, `wait_ms` |
| `click` | Cliquer sur un élément | `selector` OU `x`+`y`, `wait_ms` |
| `fill` | Remplir un champ | `selector`, `value`, `wait_ms` |
| `scroll` | Scroller | `delta_y`, `wait_ms` |
| `wait` | Pause | `wait_ms` |
| `key` | Touche clavier | `key` (ex: "Enter", "Tab"), `wait_ms` |

Le champ `text` dans chaque scène = texte lu par la voix off pendant cette scène.

## Étape 1 — Créer le script JSON

Quand l'utilisateur demande une vidéo démo, tu crées d'abord le script JSON :

1. Demande si besoin : quel scénario exactement ? (création campagne, import contacts, etc.)
2. Crée le script JSON adapté (15-25 scènes pour une vidéo de 2-3 min)
3. Sauvegarde-le dans `/data/video_scripts/`

```bash
mkdir -p /data/video_scripts
mkdir -p /data/videos

cat > /data/video_scripts/fluenzr-demo-campagne.json << 'SCRIPT_EOF'
{
  "title": "Fluenzr - Créer une campagne email en 5 minutes",
  "voice": "fr-FR-DeniseNeural",
  "output": "/data/videos/fluenzr-demo-campagne.mp4",
  "scenes": [
    ...
  ]
}
SCRIPT_EOF
```

## Étape 2 — Lancer la génération (TOUJOURS en background)

La génération prend 2-5 min → utiliser `background: true` obligatoirement :

```run-action
{"action": "bash", "background": true, "command": "python3 /skills/make-video/make_video.py --script /data/video_scripts/fluenzr-demo-campagne.json > /data/make_video.log 2>&1"}
```

Surveiller l'avancement (répéter si besoin) :

```run-action
{"action": "bash", "command": "tail -20 /data/make_video.log 2>/dev/null || echo 'Log pas encore créé'"}
```

Attendre `✅ Done:` ou `❌` dans le log.

## Étape 3 — Vérifier le résultat

```bash
# Vérifier que le fichier existe
ls -lh /data/videos/*.mp4 2>/dev/null || echo "Aucune vidéo trouvée"

# Info sur la vidéo
ffprobe -v quiet -print_format json -show_format /data/videos/fluenzr-demo-campagne.mp4 2>/dev/null | \
  python3 -c "import json,sys; f=json.load(sys.stdin)['format']; print(f'Durée: {float(f[\"duration\"]):.0f}s | Taille: {int(f[\"size\"])/1024/1024:.1f} MB')"
```

## Étape 4 — Rapport final

```
✅ Vidéo générée avec succès !

📹 Fichier : /data/videos/fluenzr-demo-campagne.mp4
⏱ Durée : 2m30s
📦 Taille : 45 MB
🎤 Voix : fr-FR-DeniseNeural
📍 Scènes : 18

Pour télécharger la vidéo :
  scp gulliver:/data/videos/fluenzr-demo-campagne.mp4 ~/Desktop/
```

## Scripts Fluenzr prêts à l'emploi

### Script 1 — Présentation générale (1 min)

Scénario : page d'accueil → tableau de bord → liste campagnes → liste contacts → stats

### Script 2 — Créer une campagne (2-3 min)

Scénario : + Nouvelle campagne → nommer → choisir template → écrire objet + corps → sélectionner audience → envoyer test → planifier

### Script 3 — Importer des contacts (1-2 min)

Scénario : Contacts → Importer → CSV → mapper les champs → confirmer → voir la liste

## Upload YouTube (optionnel)

Une fois la vidéo générée, tu peux l'uploader sur YouTube comme brouillon privé :

```bash
python3 /skills/make-video/make_video.py \
  --script /data/video_scripts/fluenzr-demo.json \
  --youtube \
  --youtube-privacy private
```

Prérequis YouTube (à faire une seule fois) :
1. Créer une chaîne YouTube sur youtube.com (compte Google de Gilles)
2. Activer "YouTube Data API v3" dans Google Cloud Console (projet `polynomial-net-188321`)
3. Ajouter `http://localhost:8090` dans les URIs de redirection du client OAuth existant
4. Lancer en LOCAL : `python3 /home/gillesah/ghdev/openNoClaw/skills/make-video/setup_youtube_auth.py --client-id XXX --client-secret YYY`
5. Déployer le token : `scp ~/ghdev/openNoClaw/data/youtube-token.json gulliver:/data/`

Vérifier si le token YouTube est configuré :
```bash
ls /data/youtube-token.json 2>/dev/null && echo "OK" || echo "MANQUANT"
```

## Credentials utiles

- **Fluenzr préprod** : `https://preprod.fluenzr.co/` — `helleugilles@gmail.com` / `6vBX6sn^C!YSjFD4YxWP`

## Règles

1. **Ne jamais inventer des URLs** — utiliser seulement des URLs fournies ou vérifiées (voir Credentials)
2. **Si une étape échoue** (selector introuvable, timeout) — noter dans le rapport et continuer
3. **Durée recommandée** : 1-3 min par vidéo (au-delà, trop long pour YouTube)
4. **Qualité audio** : Denise Neural est la meilleure voix française, toujours la préférer
5. **Si erreur FFmpeg ou edge-tts** : signaler la dépendance manquante avec la commande d'installation
